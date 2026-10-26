"""AI backend abstraction for grimwatch.

Provides a swappable Protocol so the rest of the codebase is insulated from
the Groq SDK. The groq package is imported only inside GroqBackend.__init__,
so this module (and ai.py which imports it) remain importable when groq is not
installed — which is the case for local-model-only users.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .logging_utils import get_logger, log_exception

LOGGER = get_logger("backends")

_RATE_LIMIT_MAX_ATTEMPTS = 4
_RATE_LIMIT_BASE_SECONDS = 1.5
_RATE_LIMIT_MAX_SECONDS = 20.0


@runtime_checkable
class AIBackend(Protocol):
    """Minimal contract every AI backend must satisfy.

    Callers pass model separately from the backend because local backends
    may share a base URL across multiple model names; users switch models
    without reconfiguring the endpoint.
    """

    def chat(
        self,
        model: str,
        system: str,
        user: str,
        *,
        max_tokens: int,
        temperature: float = 0.2,
        json_mode: bool = False,
        response_schema: dict[str, Any] | None = None,
    ) -> str:
        """Send one chat request and return the final text content.

        Raises RuntimeError with a user-actionable message on any terminal
        failure. Transient rate-limit errors are retried internally.
        """
        ...

    def test_connection(self, model: str) -> float:
        """Send a minimal ping and return round-trip latency in seconds.

        Raises RuntimeError if the endpoint is unreachable or returns an error.
        Used by run_setup() to validate local model config before saving it.
        """
        ...


class LocalModelUnreachableError(RuntimeError):
    """The local model endpoint refused the connection or timed out.

    Distinct from a generic RuntimeError so callers can give a more specific
    hint ('Is your local model running?') without catching all RuntimeErrors.
    """


def _is_rate_limited(message: str) -> bool:
    lowered = message.casefold()
    return "429" in message or "rate_limit" in lowered or "too many requests" in lowered


def _retry_after_seconds(message: str, attempt: int) -> float:
    """Prefer a provider-supplied Retry-After value; fall back to exponential backoff."""
    match = re.search(r"retry.?after[\"':\s]+(\d+(?:\.\d+)?)", message, re.IGNORECASE)
    if match:
        return min(float(match.group(1)), _RATE_LIMIT_MAX_SECONDS)
    return min(_RATE_LIMIT_BASE_SECONDS * (2 ** attempt), _RATE_LIMIT_MAX_SECONDS)


class GroqBackend:
    """Wraps the groq SDK. Imported lazily so the module is usable without it.

    All retry logic, GPT-OSS reasoning suppression, and strict schema handling
    from the original _chat() function lives here.
    """

    def __init__(self, api_key: str) -> None:
        # Deferred import: groq is an optional dependency for local-model users.
        try:
            from groq import Groq  # noqa: PLC0415
        except ImportError as exc:
            raise RuntimeError(
                "The 'groq' package is not installed. "
                "Install it with: pip install groq"
            ) from exc
        self._client = Groq(api_key=api_key)

    def chat(
        self,
        model: str,
        system: str,
        user: str,
        *,
        max_tokens: int,
        temperature: float = 0.2,
        json_mode: bool = False,
        response_schema: dict[str, Any] | None = None,
    ) -> str:
        """Make one bounded Groq request and require a usable final answer.

        GPT-OSS models can return reasoning separately from the final answer.
        Reasoning is explicitly disabled here so it cannot exhaust the small
        completion budget allocated for structured outputs.
        """
        request: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "max_completion_tokens": max_tokens,
            "temperature": temperature,
        }
        if model.casefold().startswith("openai/gpt-oss-"):
            request["include_reasoning"] = False
            request["reasoning_effort"] = "low"
        if response_schema is not None:
            request["response_format"] = {"type": "json_schema", "json_schema": response_schema}
        elif json_mode:
            request["response_format"] = {"type": "json_object"}

        resp = None
        last_exc: Exception | None = None
        for attempt in range(_RATE_LIMIT_MAX_ATTEMPTS):
            try:
                resp = self._client.chat.completions.create(**request)
                break
            except Exception as exc:
                log_exception(LOGGER, f"Groq request failed on attempt {attempt + 1}", exc)
                message = str(exc)
                if "413" in message or "Request too large" in message:
                    raise RuntimeError(
                        "Groq rejected the request as too large. Try again shortly."
                    ) from exc
                if not _is_rate_limited(message) or attempt == _RATE_LIMIT_MAX_ATTEMPTS - 1:
                    if _is_rate_limited(message):
                        raise RuntimeError(
                            f"Groq is rate-limiting requests; gave up after {attempt + 1} attempt(s): {exc}"
                        ) from exc
                    raise
                last_exc = exc
                time.sleep(_retry_after_seconds(message, attempt))

        if resp is None:
            raise RuntimeError("Groq rate-limited the request repeatedly.") from last_exc

        try:
            choice = resp.choices[0]
            message_obj = choice.message
            content = message_obj.content
        except (AttributeError, IndexError) as exc:
            raise RuntimeError("Groq returned no completion choices.") from exc

        if not isinstance(content, str) or not content.strip():
            finish_reason = getattr(choice, "finish_reason", None)
            reasoning = getattr(message_obj, "reasoning", None)
            detail = []
            if finish_reason:
                detail.append(f"finish_reason={finish_reason}")
            if reasoning:
                detail.append("reasoning present but no final text")
            suffix = f" ({'; '.join(detail)})" if detail else ""
            raise RuntimeError(f"Groq returned no final text{suffix}.")

        return content.strip()

    def test_connection(self, model: str) -> float:
        start = time.monotonic()
        self.chat(
            model,
            "You are a ping responder.",
            "ping",
            max_tokens=1,
            temperature=0.0,
        )
        return time.monotonic() - start


@dataclass
class OpenAICompatibleBackend:
    """Handles Ollama, LM Studio, and any OpenAI-compatible local endpoint.

    Uses only urllib.request — no new dependencies. Strict structured-output
    (response_schema) is not supported by most local runtimes; this backend
    falls back to json_mode automatically and logs a debug note.
    """

    base_url: str
    api_key: str = ""
    _timeout: int = field(default=30, repr=False)

    def _endpoint(self) -> str:
        return self.base_url.rstrip("/") + "/chat/completions"

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def chat(
        self,
        model: str,
        system: str,
        user: str,
        *,
        max_tokens: int,
        temperature: float = 0.2,
        json_mode: bool = False,
        response_schema: dict[str, Any] | None = None,
    ) -> str:
        """Send a chat completion request to the local OpenAI-compatible endpoint.

        Raises LocalModelUnreachableError on connection failure.
        Raises RuntimeError on HTTP 4xx/5xx or empty response.
        """
        if response_schema is not None:
            # Most local runtimes do not support Groq-style strict schemas;
            # fall back to json_mode so the call can still succeed.
            LOGGER.debug(
                "OpenAICompatibleBackend: response_schema not supported; "
                "falling back to json_mode for model %s",
                model,
            )
            json_mode = True

        body: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if json_mode:
            body["response_format"] = {"type": "json_object"}

        payload = json.dumps(body).encode("utf-8")
        req = Request(
            self._endpoint(),
            data=payload,
            headers=self._headers(),
            method="POST",
        )

        last_exc: Exception | None = None
        for attempt in range(_RATE_LIMIT_MAX_ATTEMPTS):
            try:
                with urlopen(req, timeout=self._timeout) as resp:
                    raw = resp.read().decode("utf-8")
            except (ConnectionRefusedError, URLError) as exc:
                cause = str(exc)
                raise LocalModelUnreachableError(
                    f"Could not reach local model at {self.base_url}: {cause}. "
                    "Is your local model running?"
                ) from exc
            except HTTPError as exc:
                body_excerpt = ""
                try:
                    body_excerpt = exc.read(256).decode("utf-8", errors="replace")
                except Exception:
                    pass
                message = f"HTTP {exc.code} from local model at {self._endpoint()}: {body_excerpt}"
                if exc.code == 429:
                    if attempt < _RATE_LIMIT_MAX_ATTEMPTS - 1:
                        last_exc = HTTPError(
                            exc.url, exc.code, exc.msg, exc.headers, None
                        )
                        time.sleep(_retry_after_seconds(str(exc.code), attempt))
                        continue
                    raise RuntimeError(
                        f"Local model rate-limited after {attempt + 1} attempts: {message}"
                    ) from exc
                raise RuntimeError(message) from exc

            try:
                data = json.loads(raw)
                content = data["choices"][0]["message"]["content"]
            except (KeyError, IndexError, json.JSONDecodeError) as exc:
                raise RuntimeError(
                    f"Local model returned unparseable response: {raw[:200]}"
                ) from exc

            if not isinstance(content, str) or not content.strip():
                raise RuntimeError("Local model returned an empty completion.")

            return content.strip()

        raise RuntimeError(
            f"Local model at {self.base_url} rate-limited all {_RATE_LIMIT_MAX_ATTEMPTS} attempts."
        ) from last_exc

    def test_connection(self, model: str) -> float:
        start = time.monotonic()
        self.chat(
            model,
            "You are a ping responder.",
            "ping",
            max_tokens=1,
            temperature=0.0,
        )
        return time.monotonic() - start

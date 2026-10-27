import re
import sys
import tempfile
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from grimwatch import ai
from grimwatch.backends import AIBackend
from grimwatch.parser import (
    ImportEntry,
    Movie,
    MovieList,
    build_merge_plan,
    coalesce_import_entries,
    duplicate_groups,
    read_import_source,
    write_classified_markdown,
)


def _fake_backend(response=None, *, side_effect=None):
    """Return a mock AIBackend whose chat() returns response or raises side_effect."""
    backend = MagicMock(spec=AIBackend)
    if side_effect is not None:
        backend.chat.side_effect = side_effect
    else:
        backend.chat.return_value = response
    return backend


class ImportAndMergeTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.archive_path = self.root / "movies.md"
        self.archive_path.write_text(
            "CRIME\n\n"
            "- [ ] L.A. Confidential (1997)\n"
            "- [ ] Suspiria (1977)\n"
            "- [ ] Suspiria (2018)\n",
            encoding="utf-8",
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_plain_markdown_import_is_not_misread_as_genre_headers(self):
        source_path = self.root / "list.md"
        source_path.write_bytes(
            "A Clockwork Orange (1971)\r\nL.A. Confidential (1997)\r\nPersona (1966)\r\n".encode("utf-8-sig")
        )

        source = read_import_source(source_path)

        self.assertEqual("plain", source.format)
        self.assertEqual("utf-8-sig", source.encoding)
        self.assertEqual(
            ["A Clockwork Orange (1971)", "L.A. Confidential (1997)", "Persona (1966)"],
            [entry.title for entry in source.entries],
        )

    def test_plain_import_skips_a_bare_section_label_before_bulleted_titles(self):
        source_path = self.root / "sectioned-list.md"
        source_path.write_text(
            "SCIENCE FICTION\n- Arrival (2016)\n- Stalker (1979)\n",
            encoding="utf-8",
        )

        source = read_import_source(source_path)

        self.assertEqual("plain", source.format)
        self.assertEqual(["Arrival (2016)", "Stalker (1979)"], [entry.title for entry in source.entries])

    def test_merge_plan_counts_new_entries_duplicates_and_watch_updates(self):
        source_path = self.root / "second.md"
        source_path.write_text(
            "# Imported list\n\n"
            "NOIR\n\n"
            "- [x] LA Confidential (1997)\n"
            "- [ ] Persona (1966)\n"
            "- [ ] Persona (1966)\n"
            "- [ ] Suspiria (2018)\n",
            encoding="utf-8",
        )

        source = read_import_source(source_path)
        archive = MovieList(self.archive_path)
        plan = build_merge_plan(source.entries, archive.all_movies())

        self.assertEqual(1, plan.source_duplicates)
        self.assertEqual(["Persona (1966)"], [entry.title for entry in plan.new_entries])
        self.assertEqual(1, len(plan.status_updates))
        self.assertEqual("L.A. Confidential (1997)", plan.status_updates[0][0].title)
        self.assertEqual(["Suspiria (2018)"], [entry.title for entry in plan.already_present])

    def test_duplicate_detection_normalizes_punctuation_but_preserves_remakes(self):
        movies = [
            Movie("L.A. Confidential (1997)", False, "NOIR", ""),
            Movie("LA Confidential (1997)", True, "CRIME", ""),
            Movie("Suspiria (1977)", False, "HORROR", ""),
            Movie("Suspiria (2018)", False, "HORROR", ""),
        ]

        groups = duplicate_groups(movies)

        self.assertEqual(1, len(groups))
        self.assertEqual({"L.A. Confidential (1997)", "LA Confidential (1997)"}, {movie.title for movie in groups[0]})

    def test_duplicate_detection_ignores_shelf_for_exact_title_and_year_matches(self):
        movies = [
            Movie("Aguirre, the Wrath of God (1972)", False, "AUTEUR CINEMA", ""),
            Movie("Aguirre, the Wrath of God (1972)", True, "EPIC HISTORICAL DRAMA", ""),
        ]

        groups = duplicate_groups(movies)

        self.assertEqual(1, len(groups))
        self.assertEqual(2, len(groups[0]))
        self.assertEqual({"AUTEUR CINEMA", "EPIC HISTORICAL DRAMA"}, {movie.genre for movie in groups[0]})

    def test_classification_companion_does_not_touch_main_archive(self):
        source_path = self.root / "import.md"
        source_path.write_text("Persona (1966)\n", encoding="utf-8")
        archive_before = self.archive_path.read_text(encoding="utf-8")
        entries, _ = coalesce_import_entries(read_import_source(source_path).entries)

        output = write_classified_markdown(
            source_path,
            entries,
            [{"title": "Persona (1966)", "genre_header": "AUTEUR CINEMA", "reason": "test"}],
        )

        self.assertEqual(self.root / "import.classified.md", output)
        self.assertIn("AUTEUR CINEMA", output.read_text(encoding="utf-8"))
        self.assertEqual(archive_before, self.archive_path.read_text(encoding="utf-8"))


class RecommendationValidationTests(unittest.TestCase):
    def setUp(self):
        self.unwatched = [
            Movie("L.A. Confidential (1997)", False, "CRIME CINEMA", ""),
            Movie("Persona (1966)", False, "AUTEUR CINEMA", ""),
            Movie("Stalker (1979)", False, "SCIENCE FICTION", ""),
        ]
        self.metadata_patch = patch.object(
            ai,
            "verify_film_metadata",
            side_effect=lambda titles, **_kwargs: [
                ai.VerifiedMetadata(
                    title=title,
                    verified=True,
                    reason="Test metadata.",
                    release_year=2000,
                    media_types=("film",),
                    countries=("Test country",),
                )
                for title in titles
            ],
        )
        self.metadata_patch.start()

    def tearDown(self):
        self.metadata_patch.stop()

    def _assert_local_unwatched(self, results):
        titles = {movie.title for movie in self.unwatched}
        self.assertTrue(results)
        self.assertTrue(all(result["title"] in titles for result in results))

    def test_numbered_markdown_title_recovers_to_a_local_movie(self):
        raw = '```json\n{"choices":[{"title":"1. **L.A. Confidential (1997)**","reason":"Perfect."}]}\n```'

        results = ai._validated_recommendations(raw, self.unwatched, self.unwatched, 1, "mood")

        self.assertEqual("L.A. Confidential (1997)", results[0]["title"])
        self._assert_local_unwatched(results)

    def test_invalid_model_title_falls_back_to_a_real_unwatched_movie(self):
        raw = '{"choices":[{"title":"A Film That Is Not In The Archive"}]}'

        results = ai._validated_recommendations(raw, self.unwatched, self.unwatched, 2, "invalid")

        self.assertEqual(2, len(results))
        self._assert_local_unwatched(results)

    def test_mood_suggestions_accept_imperfect_title_format_and_remain_local(self):
        backend = _fake_backend('{"choices":[{"title":"\\"Persona (1966)\\"","reason":"Fits."}]}')

        results = ai.suggest_movies(backend, "test-model", self.unwatched, [], mood="coming of age type movies", count=2)

        self.assertEqual("Persona (1966)", results[0]["title"])
        self._assert_local_unwatched(results)
        backend.chat.assert_called()

    def test_named_contrast_anchor_is_sent_in_the_recommendation_instruction(self):
        def fake_chat(model, system, user, **_kwargs):
            self.assertIn(
                "sharply contrast with 'Stalker (1979)' in genre, tone, pace, and era",
                user,
            )
            self.assertIn("Avoid anything from the same shelf.", user)
            return '{"choices":[{"id":1,"reason":"A deliberate change of pace."}]}'

        backend = _fake_backend()
        backend.chat.side_effect = fake_chat

        results = ai.suggest_movies(
            backend,
            "test-model",
            self.unwatched,
            [],
            contrast_against="Stalker (1979)",
            count=1,
        )

        self.assertEqual("L.A. Confidential (1997)", results[0]["title"])

    def test_external_suggestions_exclude_archive_titles_and_invalid_shelves(self):
        response = (
            '{"suggestions":['
            '{"title":"Persona (1966)","genre_number":1,"reason":"Already here."},'
            '{"title":"External Film (2001)","genre_number":999,"reason":"Invalid shelf."},'
            '{"title":"The Third Man (1949)","genre_number":1,"reason":"A classic."}'
            ']}'
        )
        backend = _fake_backend(response)

        results = ai.suggest_external_movies(
            backend, "test-model", [], ["Persona (1966)"], mood="classic noir", count=3
        )

        self.assertEqual(
            [{"title": "The Third Man (1949)", "genre": ai.GENRES[0], "reason": "A classic."}],
            results,
        )
        call_args = backend.chat.call_args
        self.assertIn("classic noir", call_args.args[2])  # user prompt
        self.assertIn("Only suggest real films with verifiable release years.", call_args.args[1])

    def test_external_suggestions_return_empty_on_model_failure(self):
        backend = _fake_backend(side_effect=RuntimeError("provider unavailable"))

        results = ai.suggest_external_movies(backend, "test-model", [], [], mood="noir")

        self.assertEqual([], results)

    def test_tonight_genre_preference_is_sent_to_the_recommender(self):
        def fake_chat(model, system, user, **_kwargs):
            self.assertIn('Limit the choice to the shelf or genre "HORROR".', user)
            return '{"id":1,"reason":"Fits the requested shelf."}'

        backend = _fake_backend()
        backend.chat.side_effect = fake_chat

        result = ai.suggest_tonight(backend, "test-model", self.unwatched, [], genre="HORROR")

        self.assertEqual("L.A. Confidential (1997)", result["title"])

    def test_blind_spots_return_known_shelves_with_local_coverage(self):
        genre_stats = [
            {"genre": "CRIME CINEMA", "watched": 1, "total": 4},
            {"genre": "HORROR", "watched": 3, "total": 3},
        ]
        backend = _fake_backend('{"gaps":[{"shelf_id":1,"reason":"Explore more noir."},{"shelf_id":99,"reason":"Ignore."}]}')

        gaps = ai.find_blind_spots(backend, "test-model", [], genre_stats)

        self.assertEqual(
            [{
                "genre": "CRIME CINEMA",
                "watched": 1,
                "total": 4,
                "coverage_percent": 25.0,
                "reason": "Explore more noir.",
            }],
            gaps,
        )

    def test_blind_spots_fall_back_to_local_coverage_when_context_is_rejected(self):
        genre_stats = [
            {"genre": "CRIME CINEMA", "watched": 1, "total": 4},
            {"genre": "HORROR", "watched": 3, "total": 3},
        ]
        backend = _fake_backend(side_effect=RuntimeError("Groq rejected the request as too large"))

        gaps = ai.find_blind_spots(backend, "test-model", [], genre_stats)

        self.assertEqual("CRIME CINEMA", gaps[0]["genre"])
        self.assertEqual("One of the least-watched shelves in your archive.", gaps[0]["reason"])

    def test_tonight_invalid_id_uses_a_verified_local_fallback(self):
        backend = _fake_backend('{"id":999,"reason":"Wrong id"}')

        result = ai.suggest_tonight(backend, "test-model", self.unwatched, [])

        self._assert_local_unwatched([result])

    @pytest.mark.xfail(reason="known recommendation fallback behavior, tracked for v1.1", strict=False)
    def test_failed_mood_request_uses_matching_local_shelf_not_an_arbitrary_movie(self):
        movies = [
            Movie("Jurassic Park (1993)", False, "ACTION CINEMA", ""),
            Movie("Portrait of a Lady on Fire (2019)", False, "ROMANTIC DRAMA — Queer Cinema", ""),
            Movie("But I'm a Cheerleader (1999)", False, "ROMANTIC DRAMA — Queer Cinema", ""),
        ]
        backend = _fake_backend(side_effect=RuntimeError("provider unavailable"))

        results = ai.suggest_movies(
            backend, "test-model", movies, [], mood="something lesbian", count=2
        )

        self.assertEqual(
            ["Portrait of a Lady on Fire (2019)", "But I'm a Cheerleader (1999)"],
            [result["title"] for result in results],
        )
        self.assertTrue(all("matched locally" in result["reason"] for result in results))

    def test_explicit_queer_mood_constrains_the_ai_candidate_pool(self):
        movies = [
            Movie("Whiplash (2014)", False, "PSYCHOLOGICAL & CHAMBER DRAMA", ""),
            Movie("Office Space (1999)", False, "AMERICAN COMEDY", ""),
            Movie("Portrait of a Lady on Fire (2019)", False, "ROMANTIC DRAMA — Queer Cinema", ""),
        ]

        def fake_chat(model, system, user, **_kwargs):
            # mood shelf matching call returns JSON; recommendation call checks pool
            if "shelves" in system.casefold():
                return '{"shelves":["ROMANTIC DRAMA — Queer Cinema"]}'
            self.assertIn("Portrait of a Lady on Fire (2019)", user)
            self.assertNotIn("Whiplash (2014)", user)
            self.assertNotIn("Office Space (1999)", user)
            return '{"choices":[{"id":1,"reason":"Matches the shelf."}]}'

        backend = _fake_backend()
        backend.chat.side_effect = fake_chat

        results = ai.suggest_movies(backend, "test-model", movies, [], mood="queer", count=1)

        self.assertEqual("Portrait of a Lady on Fire (2019)", results[0]["title"])

    def test_classification_recovers_by_id_and_marks_missing_items_for_review(self):
        response = (
            '{"classifications":[{"id":2,"genre_number":8,"confidence":4,'
            '"runner_up_genre_number":null,"reason":"Youth."}]}'
        )
        backend = _fake_backend(response)

        results = ai.classify_movies(backend, "test-model", ["Persona (1966)", "Stalker (1979)"])

        self.assertTrue(results[0]["needs_review"])
        self.assertEqual("Stalker (1979)", results[1]["title"])
        self.assertFalse(results[1]["needs_review"])

    def test_unverified_title_is_not_sent_to_the_ai_classifier(self):
        metadata = [
            ai.VerifiedMetadata.review(
                "Corrupted Input (1990)",
                "No unique exact Wikidata title match was found; classification requires review.",
            )
        ]
        backend = _fake_backend()
        with patch.object(ai, "verify_film_metadata", return_value=metadata):
            results = ai.classify_movies(backend, "test-model", ["Corrupted Input (1990)"])

        backend.chat.assert_not_called()
        self.assertTrue(results[0]["needs_review"])
        self.assertEqual(ai.UNCATEGORIZED_IMPORTS, results[0]["genre_header"])
        self.assertIn("Metadata verification required", results[0]["reason"])

    def test_classification_anchors_cover_the_fixed_taxonomy_and_render_in_the_prompt(self):
        self.assertEqual(set(ai.GENRES), set(ai.GENRE_ANCHORS))
        self.assertTrue(all(3 <= len(examples) <= 6 for examples in ai.GENRE_ANCHORS.values()))
        for genre, examples in ai.GENRE_ANCHORS.items():
            self.assertIn(f"Reference examples: {', '.join(examples)}", ai.CLASSIFY_SYSTEM)
            self.assertIn(genre, ai.GENRES)
        self.assertNotIn('Use "STAR WARS" for verified', ai.CLASSIFY_SYSTEM)
        self.assertNotIn("only for a primarily US studio-era", ai.CLASSIFY_SYSTEM)

    def test_classification_schema_requires_confidence_and_nullable_runner_up(self):
        item_schema = ai._classification_schema(2)["schema"]["properties"]["classifications"]["items"]

        self.assertEqual(
            ["id", "genre_number", "confidence", "runner_up_genre_number", "reason"],
            item_schema["required"],
        )
        self.assertEqual(1, item_schema["properties"]["confidence"]["minimum"])
        self.assertEqual(5, item_schema["properties"]["confidence"]["maximum"])
        self.assertEqual(
            ["integer", "null"],
            item_schema["properties"]["runner_up_genre_number"]["type"],
        )
        self.assertEqual(len(ai.GENRES), item_schema["properties"]["runner_up_genre_number"]["maximum"])
        self.assertFalse(ai._supports_strict_schema("qwen/qwen3.8-27b"))

    def test_classification_preserves_confidence_runner_up_and_metadata_review(self):
        from types import SimpleNamespace
        metadata = SimpleNamespace(
            verified=True,
            needs_review=True,
            reason="Fuzzy match; review recommended.",
            prompt_context="Verified metadata for a fuzzy match.",
        )
        response = (
            '{"classifications":[{"id":1,"genre_number":1,"confidence":3,'
            '"runner_up_genre_number":19,"reason":"Close between auteur and science fiction."}]}'
        )
        backend = _fake_backend(response)
        with patch.object(ai, "verify_film_metadata", return_value=[metadata]):
            results = ai.classify_movies(backend, "test-model", ["Persona (1966)"])

        self.assertEqual(3, results[0]["confidence"])
        self.assertEqual(19, results[0]["runner_up_genre_number"])
        self.assertTrue(results[0]["needs_review"])
        self.assertIn("Review: Fuzzy match; review recommended.", results[0]["reason"])

    def test_classification_rejects_invalid_confidence_and_runner_up_fields(self):
        candidate = ai._ClassificationCandidate(
            "Persona (1966)",
            ai.VerifiedMetadata(title="Persona (1966)", verified=True, reason="Test metadata."),
        )
        invalid_records = (
            '{"classifications":[{"id":1,"genre_number":1,"confidence":0,'
            '"runner_up_genre_number":19,"reason":"Bad confidence."}]}',
            '{"classifications":[{"id":1,"genre_number":1,"confidence":4,'
            '"runner_up_genre_number":1,"reason":"Same runner-up."}]}',
            '{"classifications":[{"id":1,"genre_number":1,"confidence":4,'
            '"reason":"Missing runner-up."}]}',
        )

        for response in invalid_records:
            with self.subTest(response=response):
                backend = _fake_backend(response)
                matched, request_error = ai._request_classifications(backend, "test-model", [candidate])

            self.assertEqual({}, matched)
            self.assertIsNone(request_error)

    def test_classification_forwards_optional_metadata_provider_settings(self):
        backend = _fake_backend('{"classifications":[]}')
        with patch.object(ai, "verify_film_metadata", return_value=[]) as verify:
            ai.classify_movies(
                backend,
                "test-model",
                ["Persona (1966)"],
                tmdb_api_key="tmdb",
                omdb_api_key="omdb",
                refresh_metadata=True,
            )

        verify.assert_called_once_with(
            ["Persona (1966)"],
            tmdb_api_key="tmdb",
            omdb_api_key="omdb",
            refresh_metadata=True,
        )

    def test_classification_retries_only_missing_records_from_a_partial_response(self):
        first_batch = (
            '{"classifications":[{"id":1,"genre_number":1,"confidence":5,'
            '"runner_up_genre_number":null,"reason":"First."}]}'
        )
        retry_batch = (
            '{"classifications":[{"id":1,"genre_number":19,"confidence":4,'
            '"runner_up_genre_number":1,"reason":"Second."}]}'
        )
        backend = _fake_backend()
        backend.chat.side_effect = [first_batch, retry_batch]

        results = ai.classify_movies(backend, "test-model", ["Persona (1966)", "Stalker (1979)"])

        self.assertEqual(2, backend.chat.call_count)
        self.assertEqual(["Persona (1966)", "Stalker (1979)"], [result["title"] for result in results])
        self.assertTrue(all(not result["needs_review"] for result in results))
        self.assertEqual(ai.GENRES[0], results[0]["genre_header"])
        self.assertEqual(ai.GENRES[18], results[1]["genre_header"])

    def test_gpt_oss_classification_uses_a_strict_schema_without_an_exact_count(self):
        captured = {}

        def fake_chat(model, system, user, **kwargs):
            captured.update(kwargs)
            return (
                '{"classifications":['
                '{"id":1,"genre_number":1,"confidence":5,"runner_up_genre_number":null,"reason":"One."},'
                '{"id":2,"genre_number":19,"confidence":4,"runner_up_genre_number":1,"reason":"Two."}]}'
            )

        backend = _fake_backend()
        backend.chat.side_effect = fake_chat

        ai.classify_movies(backend, "openai/gpt-oss-120b", ["Persona (1966)", "Stalker (1979)"])

        schema = captured["response_schema"]
        self.assertTrue(schema["strict"])
        self.assertNotIn("minItems", schema["schema"]["properties"]["classifications"])
        self.assertEqual(2, schema["schema"]["properties"]["classifications"]["maxItems"])
        self.assertNotIn("json_mode", captured)

    def test_classification_max_tokens_scales_with_batch_size(self):
        captured = []

        def fake_chat(model, system, user, **kwargs):
            captured.append(kwargs["max_tokens"])
            return (
                '{"classifications":[{"id":1,"genre_number":1,"confidence":5,'
                '"runner_up_genre_number":null,"reason":"One."}]}'
            )

        backend = _fake_backend()
        backend.chat.side_effect = fake_chat

        ai.classify_movies(backend, "test-model", ["Persona (1966)"] * 8)

        self.assertGreater(captured[0], 700)

    def test_partial_array_response_is_locally_accepted_and_gaps_retried(self):
        partial = (
            '{"classifications":[{"id":1,"genre_number":1,"confidence":5,'
            '"runner_up_genre_number":null,"reason":"First."}]}'
        )
        retry = (
            '{"classifications":[{"id":1,"genre_number":19,"confidence":4,'
            '"runner_up_genre_number":1,"reason":"Second."}]}'
        )
        backend = _fake_backend()
        backend.chat.side_effect = [partial, retry]

        results = ai.classify_movies(backend, "openai/gpt-oss-120b", ["Persona (1966)", "Stalker (1979)"])

        self.assertEqual(2, backend.chat.call_count)
        self.assertFalse(any(result["needs_review"] for result in results))
        self.assertEqual(ai.GENRES[0], results[0]["genre_header"])
        self.assertEqual(ai.GENRES[18], results[1]["genre_header"])

    def test_hard_json_validate_failed_rejection_only_fails_that_batch_and_retries(self):
        schema_error = RuntimeError(
            "Error code: 400 - jsonschema: '/classifications' does not validate "
            "with /properties/classifications/minItems: minimum 16 items "
            "required, but found 8 items"
        )
        recovered = (
            '{"classifications":[{"id":1,"genre_number":1,"confidence":5,'
            '"runner_up_genre_number":null,"reason":"Recovered."}]}'
        )
        backend = _fake_backend()
        backend.chat.side_effect = [schema_error, recovered]

        results = ai.classify_movies(backend, "openai/gpt-oss-120b", ["Persona (1966)"])

        self.assertEqual(2, backend.chat.call_count)
        self.assertFalse(results[0]["needs_review"])
        self.assertEqual(ai.GENRES[0], results[0]["genre_header"])

    def test_titles_unresolved_after_every_retry_size_are_marked_for_review_not_silently_dropped(self):
        def fake_chat(model, system, user, **_kwargs):
            if "Persistent Failure" in user:
                raise RuntimeError("Error code: 400 - json_validate_failed")
            return (
                '{"classifications":[{"id":1,"genre_number":1,"confidence":5,'
                '"runner_up_genre_number":null,"reason":"Ok."}]}'
            )

        backend = _fake_backend()
        backend.chat.side_effect = fake_chat

        results = ai.classify_movies(backend, "openai/gpt-oss-120b", ["Persistent Failure (2000)"])

        self.assertTrue(results[0]["needs_review"])
        self.assertEqual(ai.UNCATEGORIZED_IMPORTS, results[0]["genre_header"])
        self.assertIn("json_validate_failed", results[0]["reason"])

    def test_seventy_one_title_import_maps_to_seventy_one_rows_under_partial_responses(self):
        titles = [f"Film {i} (2000)" for i in range(1, 72)]

        def fake_chat(model, system, user, **_kwargs):
            requested = re.findall(r"^\[(\d+)\]", user, re.MULTILINE)
            ids = [int(i) for i in requested]
            half = ids[: max(1, len(ids) // 2)]
            records = ",".join(
                f'{{"id":{i},"genre_number":1,"confidence":5,'
                f'"runner_up_genre_number":null,"reason":"r"}}' for i in half
            )
            return '{"classifications":[' + records + "]}"

        backend = _fake_backend()
        backend.chat.side_effect = fake_chat

        results = ai.classify_movies(backend, "openai/gpt-oss-120b", titles)

        self.assertEqual(71, len(results))
        self.assertEqual(titles, [result["title"] for result in results])
        self.assertTrue(all(not result["needs_review"] for result in results))

    def test_short_error_trims_the_embedded_failed_generation_payload(self):
        huge_payload = "x" * 2000
        exc = RuntimeError(
            "Error code: 400 - {'error': {'message': \"Generated JSON does not "
            "match the expected schema.\", 'code': 'json_validate_failed', "
            f"'failed_generation': '{huge_payload}'}}}}"
        )

        result = ai._short_error(exc)

        self.assertLess(len(result), 200)
        self.assertNotIn("x" * 50, result)
        self.assertIn("json_validate_failed", result)

    def test_unresolved_title_reason_is_trimmed_not_a_raw_json_dump(self):
        huge_payload = "y" * 2000
        schema_error = RuntimeError(
            "Error code: 400 - json_validate_failed "
            f"'failed_generation': '{huge_payload}'"
        )
        backend = _fake_backend(side_effect=schema_error)

        results = ai.classify_movies(backend, "openai/gpt-oss-120b", ["Persistent Failure (2000)"])

        self.assertTrue(results[0]["needs_review"])
        self.assertLess(len(results[0]["reason"]), 250)
        self.assertNotIn("y" * 50, results[0]["reason"])

    def test_rate_limit_is_retried_with_backoff_instead_of_failing_immediately(self):
        """GroqBackend internal retry — test via the backend directly."""
        from grimwatch.backends import GroqBackend
        import time as time_module

        rate_limited = RuntimeError("Error code: 429 - rate_limit_exceeded, please retry after 0")
        calls = {"count": 0}

        class FakeCompletions:
            def create(self, **_request):
                calls["count"] += 1
                if calls["count"] == 1:
                    raise rate_limited
                return SimpleNamespace(
                    choices=[SimpleNamespace(
                        message=SimpleNamespace(content="ok", reasoning=None),
                        finish_reason="stop",
                    )]
                )

        from types import SimpleNamespace as SN

        backend = GroqBackend.__new__(GroqBackend)
        backend._client = SN(chat=SN(completions=FakeCompletions()))

        with patch.object(time_module, "sleep", return_value=None):
            result = backend.chat("test-model", "system", "user", max_tokens=10)

        self.assertEqual("ok", result)
        self.assertEqual(2, calls["count"])


class GroqBackendContractTests(unittest.TestCase):
    """Tests for GroqBackend request shape — GPT-OSS params, schema, empty response handling."""

    def _backend_with_response(self, content, *, finish_reason="stop", reasoning=None):
        from grimwatch.backends import GroqBackend
        from types import SimpleNamespace as SN

        captured = {}

        class FakeCompletions:
            def create(self, **request):
                captured.update(request)
                return SN(
                    choices=[SN(
                        message=SN(content=content, reasoning=reasoning),
                        finish_reason=finish_reason,
                    )]
                )

        backend = GroqBackend.__new__(GroqBackend)
        backend._client = SN(chat=SN(completions=FakeCompletions()))
        return backend, captured

    def test_gpt_oss_request_disables_reasoning_and_uses_current_token_parameter(self):
        backend, request = self._backend_with_response('{"id": 1}')

        result = backend.chat(
            "openai/gpt-oss-120b",
            "system",
            "user",
            max_tokens=321,
            json_mode=True,
        )

        self.assertEqual('{"id": 1}', result)
        self.assertEqual(321, request["max_completion_tokens"])
        self.assertNotIn("max_tokens", request)
        self.assertFalse(request["include_reasoning"])
        self.assertEqual("low", request["reasoning_effort"])
        self.assertEqual({"type": "json_object"}, request["response_format"])

    def test_strict_schema_overrides_json_object_mode(self):
        backend, request = self._backend_with_response('{"classifications":[]}')
        schema = {"name": "test", "strict": True, "schema": {"type": "object"}}

        backend.chat("openai/gpt-oss-120b", "system", "user", max_tokens=10, json_mode=True, response_schema=schema)

        self.assertEqual({"type": "json_schema", "json_schema": schema}, request["response_format"])

    def test_empty_gpt_oss_final_response_reports_provider_metadata(self):
        backend, _ = self._backend_with_response(
            None,
            finish_reason="length",
            reasoning="internal chain of thought",
        )

        with self.assertRaisesRegex(
            RuntimeError,
            r"no final text \(finish_reason=length; reasoning present but no final text\)",
        ):
            backend.chat("openai/gpt-oss-120b", "system", "user", max_tokens=10)


if __name__ == "__main__":
    unittest.main()

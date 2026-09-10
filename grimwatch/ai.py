"""Groq AI client — classification, suggestions, blind spots, year enrichment."""

import hashlib
import json
import re
import time
from dataclasses import dataclass
from typing import Any, Optional, Sequence
from groq import Groq

from .logging_utils import get_logger, log_exception
from .metadata import VerifiedMetadata, verify_film_metadata
from .parser import (
    Movie,
    clean_display_title,
    extract_title_year,
    normalized_title_key,
    resolve_movie_reference,
    titles_equivalent,
)


LOGGER = get_logger("ai")

GENRES = [
    "AUTEUR CINEMA — European Philosophical & Metaphysical Drama (Bergman, Tarkovsky, Antonioni lineage; existential inquiry, spiritual dread, formal rigor)",
    "AUTEUR CINEMA — French New Wave & Its Legacy (Godard, Truffaut, Varda, Rohmer; rupture with classical cinema, self-awareness, improvisation)",
    "Kieslowski & Polish-European Moral Cinema (Moral ambiguity, quiet devastation, color symbolism, humanist weight)",
    "CLASSICAL HOLLYWOOD PRESTIGE DRAMA (Studio-era craftsmanship, grand narrative, moral clarity, emotional magnitude)",
    "PRESTIGE LITERARY & HISTORICAL DRAMA (Adaptation, biography, period setting; awards-driven but cinematically serious)",
    "SOCIAL REALISM & POLITICAL DRAMA (Working class, systemic injustice, documentary aesthetic, social urgency)",
    "PSYCHOLOGICAL & CHAMBER DRAMA (Confined spaces, internal collapse, performance-driven; few characters, maximum pressure)",
    "COMING-OF-AGE & YOUTH DRAMA (Adolescent identity, loss of innocence, formative rupture)",
    "MELODRAMA & EMOTIONAL MAINSTREAM DRAMA (Broad emotional register, crowd-pleasing pathos, popular appeal)",
    "BIOGRAPHICAL DRAMA — Artists & Intellectuals (Painters, composers, poets; inner life externalized through period setting)",
    "BIOGRAPHICAL DRAMA — Ambition, Industry & Sport (Great men and institutions; achievement, hubris, system)",
    "PRESTIGE TELEVISION DRAMA (Long-form narrative; cinematic ambition in serialized form)",
    "CRIME CINEMA — Auteur & Art-House Crime (Character over plot, moral weight, crime as philosophical subject)",
    "CRIME CINEMA — French Polar (Fatalism, style, silence, masculine codes; Melville and heirs)",
    "CRIME CINEMA — American Neo-Noir & Crime Thriller (Moral ambiguity, urban rot, corrupt systems, stylized violence)",
    "CRIME CINEMA — Pulp, Exploitation & Stylized Genre Crime (Camp, excess, irony, genre pleasure over moral seriousness)",
    "PSYCHOLOGICAL THRILLER & MYSTERY — Hitchcockian & Paranoia Cinema (Identity fracture, unreliable reality, paranoia; Hitchcock lineage)",
    "SCI-FI — Action & Spectacle",
    "SCIENCE FICTION — Philosophical / Cerebral Sci-Fi Canon",
    "EPIC HISTORICAL DRAMA — Auteur & Art-House (Grand scale with auteur vision or genuine moral inquiry)",
    "EPIC HISTORICAL DRAMA — Classical prestige cinema emphasizing large-scale period spectacle, craftsmanship, and scope over experimentation.",
    "BRITISH IMPERIAL & COLONIAL ADVENTURE CINEMA (Empire, colonial campaigns, and imperial adventure mythology)",
    "BIBLICAL & ANCIENT WORLD EPIC (SWORD & SANDAL)",
    "JOAN OF ARC CYCLE",
    "WAR CINEMA — Immersive & Combat-Focused (Experiential, visceral, you-are-there filmmaking; war as physical ordeal)",
    "WAR CINEMA — Ideological, Moral & Political (War examined through conscience, collaboration, resistance, complicity)",
    "WESTERN — Classical & Mythological (Ford, Mann, Hawks tradition; landscape as moral space, the law and its limits)",
    "WESTERN — Revisionist & Neo-Western (Genre deconstruction, irony, brutality as critique)",
    "MEDIEVAL & DARK AGES EPIC (Pre-gunpowder war, Norse, Celtic, Crusader; physicality, myth, mud)",
    "HIGH FANTASY EPIC (World-building at maximum scale; myth, quest, transcendence)",
    "SUPERHERO CINEMA — Auteur & Prestige (Director-driven, thematically serious, character-first)",
    "STAR WARS (Chronological in-universe order preserved)",
    "ACTION CINEMA — Crafted & Kinetic (Direction, choreography, or world-building elevates genre; style with substance)",
    "ACTION CINEMA — Mainstream Blockbuster & Crowd-Pleaser (Entertainment-first; spectacle, stars, straightforward propulsion)",
    "ROMANTIC DRAMA — Slow Cinema & Emotional Realism (Conversation, time, longing; cinema as lived duration)",
    "ROMANTIC DRAMA — Queer Cinema (Desire, identity, gaze; formally or thematically centered on queer experience)",
    "ROMANTIC DRAMA — Popular & Mainstream (Warmth, sentiment, broad appeal; entertainment over formal ambition)",
    "EROTIC CINEMA — Art-House & Transgressive (Sexuality as formal or philosophical subject; auteur-driven, international prestige tradition)",
    "EROTIC CINEMA — Exploitation & Softcore (Primarily sexual content with minimal artistic ambition)",
    "HORROR — Elevated & Art-House (Dread, atmosphere, psychological depth; horror as vehicle for serious themes)",
    "HORROR — French Extreme (New French Extremity) (Bodily horror, transgression, physical and psychological punishment)",
    "HORROR — Body Horror & Transgressive (The flesh as battlefield; Cronenberg lineage)",
    "HORROR — Mainstream Genre & Franchise (Entertainment-first horror; jump scares, sequels, mass audience)",
    "EXPERIMENTAL & AVANT-GARDE CINEMA (Form as content; cinema interrogating its own nature)",
    "SPIRITUAL & RELIGIOUS CINEMA (Faith, doubt, martyrdom, the sacred; cinema as theological inquiry)",
    "FRENCH COMEDY — Tati & Poetic Whimsy (Gentle absurdism, visual comedy, humanist warmth)",
    "FRENCH COMEDY — Populist & Satirical (Social comedy, cultural friction, mainstream French entertainment)",
    "FRENCH THRILLER & CINÉMA DU LOOK (Style, neon, romanticism, alienation; Besson/Beineix/Carax 1980s visual movement)",
    "AMERICAN COMEDY — Satirical & Irreverent (Genre parody, political satire, formal subversion)",
    "AMERICAN COMEDY — Teen & High School (Adolescent social world; cliques, rebellion, sexual awakening)",
    "AMERICAN COMEDY — Fratboy, Party & Gross-Out (Raunch, male bonding, arrested development)",
    "PRESTIGE TELEVISION COMEDY & DRAMA (Serialized long-form; cinematic ambition, cultural impact)",
    "MUSICAL THEATRE & CLASSICAL MOVIE MUSICAL (Song-and-dance as primary narrative vehicle; golden age Hollywood tradition)",
    "ROCK MUSICAL & CULT MUSICAL (Underground, transgressive, or punk-inflected musical cinema)",
    "PRE-CODE HOLLYWOOD (1929–JUNE 1934) (US studio films released before rigorous Production Code enforcement)",
    "NORDIC CRIME & SOCIAL THRILLER (Scandinavian tradition; institutional dread, moral exposure, minimalism)",
    "Documentaries",
    "FILM NOIR & HOLLYWOOD GOTHIC (Fatalism, shadow, corruption, and self-reflexive studio-era darkness)",
    "SCI-FI COMEDY & PARODY (Science-fiction concepts used primarily for comedy, satire, or genre parody)",
    "ACTION-THRILLER & ROAD HORROR (Pursuit, danger, and survival on the road or in transit)",
    "ANIMATION & FAMILY FANTASY (Animated features and fantastical all-ages storytelling)",
    "DOCUDRAMA & HISTORICAL TELEVISION (Scripted factual reconstruction, biography, or historical serial drama)",
]

# Static taxonomy examples define category boundaries for the classifier. They
# are deliberately serialized into the prompt only; they are never consulted
# as a title lookup or a classification-routing rule.
GENRE_ANCHORS: dict[str, tuple[str, ...]] = {
    "AUTEUR CINEMA — European Philosophical & Metaphysical Drama (Bergman, Tarkovsky, Antonioni lineage; existential inquiry, spiritual dread, formal rigor)": (
        "Persona (1966)", "The Seventh Seal (1957)", "Andrei Rublev (1966)",
    ),
    "AUTEUR CINEMA — French New Wave & Its Legacy (Godard, Truffaut, Varda, Rohmer; rupture with classical cinema, self-awareness, improvisation)": (
        "Breathless (1960)", "The 400 Blows (1959)", "Cléo from 5 to 7 (1962)",
    ),
    "Kieslowski & Polish-European Moral Cinema (Moral ambiguity, quiet devastation, color symbolism, humanist weight)": (
        "Three Colours: Blue (1993)", "Dekalog (1988)", "A Short Film About Killing (1988)",
    ),
    "CLASSICAL HOLLYWOOD PRESTIGE DRAMA (Studio-era craftsmanship, grand narrative, moral clarity, emotional magnitude)": (
        "Citizen Kane (1941)", "Casablanca (1942)", "All About Eve (1950)",
    ),
    "PRESTIGE LITERARY & HISTORICAL DRAMA (Adaptation, biography, period setting; awards-driven but cinematically serious)": (
        "Schindler's List (1993)", "Amadeus (1984)", "The English Patient (1996)",
    ),
    "SOCIAL REALISM & POLITICAL DRAMA (Working class, systemic injustice, documentary aesthetic, social urgency)": (
        "Bicycle Thieves (1948)", "The Battle of Algiers (1966)", "La Haine (1995)",
    ),
    "PSYCHOLOGICAL & CHAMBER DRAMA (Confined spaces, internal collapse, performance-driven; few characters, maximum pressure)": (
        "12 Angry Men (1957)", "My Dinner with Andre (1981)", "Locke (2013)",
    ),
    "COMING-OF-AGE & YOUTH DRAMA (Adolescent identity, loss of innocence, formative rupture)": (
        "Stand by Me (1986)", "Boyhood (2014)", "Rebel Without a Cause (1955)",
    ),
    "MELODRAMA & EMOTIONAL MAINSTREAM DRAMA (Broad emotional register, crowd-pleasing pathos, popular appeal)": (
        "All That Heaven Allows (1955)", "Imitation of Life (1959)", "Terms of Endearment (1983)",
    ),
    "BIOGRAPHICAL DRAMA — Artists & Intellectuals (Painters, composers, poets; inner life externalized through period setting)": (
        "Amadeus (1984)", "Pollock (2000)", "Basquiat (1996)",
    ),
    "BIOGRAPHICAL DRAMA — Ambition, Industry & Sport (Great men and institutions; achievement, hubris, system)": (
        "Raging Bull (1980)", "The Wolf of Wall Street (2013)", "Ford v Ferrari (2019)",
    ),
    "PRESTIGE TELEVISION DRAMA (Long-form narrative; cinematic ambition in serialized form)": (
        "The Sopranos (1999)", "Deadwood (2004)", "Rome (2005)",
    ),
    "CRIME CINEMA — Auteur & Art-House Crime (Character over plot, moral weight, crime as philosophical subject)": (
        "The Godfather (1972)", "Goodfellas (1990)", "Chinatown (1974)",
    ),
    "CRIME CINEMA — French Polar (Fatalism, style, silence, masculine codes; Melville and heirs)": (
        "Rififi (1955)", "Le Samouraï (1967)", "Le Cercle Rouge (1970)",
    ),
    "CRIME CINEMA — American Neo-Noir & Crime Thriller (Moral ambiguity, urban rot, corrupt systems, stylized violence)": (
        "Heat (1995)", "Se7en (1995)", "L.A. Confidential (1997)",
    ),
    "CRIME CINEMA — Pulp, Exploitation & Stylized Genre Crime (Camp, excess, irony, genre pleasure over moral seriousness)": (
        "Pulp Fiction (1994)", "Reservoir Dogs (1992)", "Natural Born Killers (1994)",
    ),
    "PSYCHOLOGICAL THRILLER & MYSTERY — Hitchcockian & Paranoia Cinema (Identity fracture, unreliable reality, paranoia; Hitchcock lineage)": (
        "Vertigo (1958)", "Rear Window (1954)", "Mulholland Drive (2001)",
    ),
    "SCI-FI — Action & Spectacle": (
        "Alien (1979)", "The Matrix (1999)", "The Terminator (1984)",
    ),
    "SCIENCE FICTION — Philosophical / Cerebral Sci-Fi Canon": (
        "Stalker (1979)", "2001: A Space Odyssey (1968)", "Solaris (1972)",
    ),
    "EPIC HISTORICAL DRAMA — Auteur & Art-House (Grand scale with auteur vision or genuine moral inquiry)": (
        "Lawrence of Arabia (1962)", "Seven Samurai (1954)", "Barry Lyndon (1975)",
    ),
    "EPIC HISTORICAL DRAMA — Classical prestige cinema emphasizing large-scale period spectacle, craftsmanship, and scope over experimentation.": (
        "Becket (1964)", "The Lion in Winter (1968)", "El Cid (1961)",
    ),
    "BRITISH IMPERIAL & COLONIAL ADVENTURE CINEMA (Empire, colonial campaigns, and imperial adventure mythology)": (
        "Zulu (1964)", "The Four Feathers (1939)", "Gunga Din (1939)",
    ),
    "BIBLICAL & ANCIENT WORLD EPIC (SWORD & SANDAL)": (
        "Ben-Hur (1959)", "Spartacus (1960)", "The Ten Commandments (1956)",
    ),
    "JOAN OF ARC CYCLE": (
        "The Passion of Joan of Arc (1928)", "Joan of Arc (1948)", "The Messenger: The Story of Joan of Arc (1999)",
    ),
    "WAR CINEMA — Immersive & Combat-Focused (Experiential, visceral, you-are-there filmmaking; war as physical ordeal)": (
        "Saving Private Ryan (1998)", "1917 (2019)", "Full Metal Jacket (1987)",
    ),
    "WAR CINEMA — Ideological, Moral & Political (War examined through conscience, collaboration, resistance, complicity)": (
        "Come and See (1985)", "The Battle of Algiers (1966)", "Apocalypse Now (1979)",
    ),
    "WESTERN — Classical & Mythological (Ford, Mann, Hawks tradition; landscape as moral space, the law and its limits)": (
        "The Searchers (1956)", "Stagecoach (1939)", "Red River (1948)",
    ),
    "WESTERN — Revisionist & Neo-Western (Genre deconstruction, irony, brutality as critique)": (
        "Unforgiven (1992)", "True Grit (2010)", "Django Unchained (2012)",
    ),
    "MEDIEVAL & DARK AGES EPIC (Pre-gunpowder war, Norse, Celtic, Crusader; physicality, myth, mud)": (
        "Excalibur (1981)", "The Northman (2022)", "Kingdom of Heaven (2005)",
    ),
    "HIGH FANTASY EPIC (World-building at maximum scale; myth, quest, transcendence)": (
        "The Lord of the Rings: The Fellowship of the Ring (2001)", "The Wizard of Oz (1939)", "The Chronicles of Narnia: The Lion, the Witch and the Wardrobe (2005)",
    ),
    "SUPERHERO CINEMA — Auteur & Prestige (Director-driven, thematically serious, character-first)": (
        "The Dark Knight (2008)", "Batman Begins (2005)", "Spider-Man: Into the Spider-Verse (2018)",
    ),
    "STAR WARS (Chronological in-universe order preserved)": (
        "Star Wars: Episode IV – A New Hope (1977)", "The Empire Strikes Back (1980)", "The Mandalorian (2019)",
    ),
    "ACTION CINEMA — Crafted & Kinetic (Direction, choreography, or world-building elevates genre; style with substance)": (
        "Mad Max: Fury Road (2015)", "Raiders of the Lost Ark (1981)", "Terminator 2: Judgment Day (1991)",
    ),
    "ACTION CINEMA — Mainstream Blockbuster & Crowd-Pleaser (Entertainment-first; spectacle, stars, straightforward propulsion)": (
        "Top Gun (1986)", "Jurassic Park (1993)", "Rush Hour (1998)",
    ),
    "ROMANTIC DRAMA — Slow Cinema & Emotional Realism (Conversation, time, longing; cinema as lived duration)": (
        "Before Sunrise (1995)", "Before Sunset (2004)", "Blue Valentine (2010)",
    ),
    "ROMANTIC DRAMA — Queer Cinema (Desire, identity, gaze; formally or thematically centered on queer experience)": (
        "Portrait of a Lady on Fire (2019)", "Carol (2015)", "Brokeback Mountain (2005)",
    ),
    "ROMANTIC DRAMA — Popular & Mainstream (Warmth, sentiment, broad appeal; entertainment over formal ambition)": (
        "Pride and Prejudice (2005)", "When Harry Met Sally... (1989)", "Notting Hill (1999)",
    ),
    "EROTIC CINEMA — Art-House & Transgressive (Sexuality as formal or philosophical subject; auteur-driven, international prestige tradition)": (
        "In the Realm of the Senses (1976)", "The Piano Teacher (2001)", "Belle de Jour (1967)",
    ),
    "EROTIC CINEMA — Exploitation & Softcore (Primarily sexual content with minimal artistic ambition)": (
        "Showgirls (1995)", "The Girl Next Door (2004)", "Don Jon (2013)",
    ),
    "HORROR — Elevated & Art-House (Dread, atmosphere, psychological depth; horror as vehicle for serious themes)": (
        "The Shining (1980)", "Hereditary (2018)", "The Witch (2015)",
    ),
    "HORROR — French Extreme (New French Extremity) (Bodily horror, transgression, physical and psychological punishment)": (
        "Martyrs (2008)", "High Tension (2003)", "Inside (2007)",
    ),
    "HORROR — Body Horror & Transgressive (The flesh as battlefield; Cronenberg lineage)": (
        "Videodrome (1983)", "The Fly (1986)", "The Thing (1982)",
    ),
    "HORROR — Mainstream Genre & Franchise (Entertainment-first horror; jump scares, sequels, mass audience)": (
        "The Exorcist (1973)", "Jaws (1975)", "Poltergeist (1982)",
    ),
    "EXPERIMENTAL & AVANT-GARDE CINEMA (Form as content; cinema interrogating its own nature)": (
        "Un Chien Andalou (1929)", "Meshes of the Afternoon (1943)", "Eraserhead (1977)",
    ),
    "SPIRITUAL & RELIGIOUS CINEMA (Faith, doubt, martyrdom, the sacred; cinema as theological inquiry)": (
        "Silence (2016)", "Babette's Feast (1987)", "The Passion of Joan of Arc (1928)",
    ),
    "FRENCH COMEDY — Tati & Poetic Whimsy (Gentle absurdism, visual comedy, humanist warmth)": (
        "Playtime (1967)", "Mon Oncle (1958)", "The Red Balloon (1956)",
    ),
    "FRENCH COMEDY — Populist & Satirical (Social comedy, cultural friction, mainstream French entertainment)": (
        "The Dinner Game (1998)", "OSS 117: Cairo, Nest of Spies (2006)", "Intouchables (2011)",
    ),
    "FRENCH THRILLER & CINÉMA DU LOOK (Style, neon, romanticism, alienation; Besson/Beineix/Carax 1980s visual movement)": (
        "Diva (1981)", "Mauvais Sang (1986)", "Subway (1985)",
    ),
    "AMERICAN COMEDY — Satirical & Irreverent (Genre parody, political satire, formal subversion)": (
        "Dr. Strangelove (1964)", "This Is Spinal Tap (1984)", "Blazing Saddles (1974)",
    ),
    "AMERICAN COMEDY — Teen & High School (Adolescent social world; cliques, rebellion, sexual awakening)": (
        "The Breakfast Club (1985)", "Dazed and Confused (1993)", "Ferris Bueller's Day Off (1986)",
    ),
    "AMERICAN COMEDY — Fratboy, Party & Gross-Out (Raunch, male bonding, arrested development)": (
        "National Lampoon's Animal House (1978)", "Old School (2003)", "The Hangover (2009)",
    ),
    "PRESTIGE TELEVISION COMEDY & DRAMA (Serialized long-form; cinematic ambition, cultural impact)": (
        "Freaks and Geeks (1999)", "Seinfeld (1989)", "Atlanta (2016)",
    ),
    "MUSICAL THEATRE & CLASSICAL MOVIE MUSICAL (Song-and-dance as primary narrative vehicle; golden age Hollywood tradition)": (
        "Singin' in the Rain (1952)", "Cabaret (1972)", "The Sound of Music (1965)",
    ),
    "ROCK MUSICAL & CULT MUSICAL (Underground, transgressive, or punk-inflected musical cinema)": (
        "The Rocky Horror Picture Show (1975)", "Hedwig and the Angry Inch (2001)", "Tommy (1975)",
    ),
    "PRE-CODE HOLLYWOOD (1929–JUNE 1934) (US studio films released before rigorous Production Code enforcement)": (
        "Baby Face (1933)", "Red Dust (1932)", "I'm No Angel (1933)",
    ),
    "NORDIC CRIME & SOCIAL THRILLER (Scandinavian tradition; institutional dread, moral exposure, minimalism)": (
        "The Hunt (2012)", "Let the Right One In (2008)", "The Girl with the Dragon Tattoo (2009)",
    ),
    "Documentaries": (
        "Shoah (1985)", "Harlan County USA (1976)", "The Act of Killing (2012)",
    ),
    "FILM NOIR & HOLLYWOOD GOTHIC (Fatalism, shadow, corruption, and self-reflexive studio-era darkness)": (
        "Double Indemnity (1944)", "The Big Sleep (1946)", "Out of the Past (1947)",
    ),
    "SCI-FI COMEDY & PARODY (Science-fiction concepts used primarily for comedy, satire, or genre parody)": (
        "Galaxy Quest (1999)", "Spaceballs (1987)", "Men in Black (1997)",
    ),
    "ACTION-THRILLER & ROAD HORROR (Pursuit, danger, and survival on the road or in transit)": (
        "Mad Max (1979)", "The Road Warrior (1981)", "Joy Ride (2001)",
    ),
    "ANIMATION & FAMILY FANTASY (Animated features and fantastical all-ages storytelling)": (
        "Toy Story (1995)", "Spirited Away (2001)", "Finding Nemo (2003)",
    ),
    "DOCUDRAMA & HISTORICAL TELEVISION (Scripted factual reconstruction, biography, or historical serial drama)": (
        "Chernobyl (2019)", "The Voyage of Charles Darwin (1978)", "Band of Brothers (2001)",
    ),
}


def _validate_genre_anchors() -> None:
    """Fail fast if prompt examples drift from the fixed taxonomy."""
    missing = set(GENRES) - set(GENRE_ANCHORS)
    extra = set(GENRE_ANCHORS) - set(GENRES)
    invalid = [genre for genre, examples in GENRE_ANCHORS.items() if not 3 <= len(examples) <= 6]
    if missing or extra or invalid:
        detail = []
        if missing:
            detail.append(f"missing: {sorted(missing)!r}")
        if extra:
            detail.append(f"extra: {sorted(extra)!r}")
        if invalid:
            detail.append(f"invalid example count: {invalid!r}")
        raise RuntimeError("GENRE_ANCHORS must cover every GENRES entry with 3–6 examples (" + "; ".join(detail) + ")")


_validate_genre_anchors()

GENRE_LIST_STR = "\n".join(f"{i+1}. {g}" for i, g in enumerate(GENRES))


def _classification_label(genre: str) -> str:
    """Keep the category distinctions while avoiding a huge repeated prompt."""
    label = re.sub(r"\s*\([^()]*\)", "", genre)
    return " ".join(label.split()).strip(" .")[:115]


CLASSIFICATION_GENRE_LIST_STR = "\n".join(
    f"{index}. {_classification_label(genre)}\n"
    f"   Reference examples: {', '.join(GENRE_ANCHORS[genre])}"
    for index, genre in enumerate(GENRES, 1)
)

# Keep recommendation requests far below the 8K TPM limit. Candidate IDs make
# validation local and eliminate brittle "return the exact title" prompting.
RECOMMENDATION_CONTEXT_CHARS = 7200
RECOMMENDATION_CONTEXT_ITEMS = 120
# A 50-film request exceeded the available completion budget in real use, and
# even 16 per request regularly ran out of budget before GPT-OSS finished the
# array (see _classification_max_tokens below). Batches start small, and any
# titles a batch fails to resolve are retried in progressively smaller groups
# — 4, then 1 at a time — instead of ever being silently marked uncategorized
# after just one attempt.
CLASSIFICATION_BATCH_SIZE = 20
CLASSIFICATION_RETRY_BATCH_SIZES = (4, 1)
# Groq's strict structured-output mode uses constrained decoding: it cannot
# invent tokens beyond the completion budget, so a batch that needs more room
# than it's given simply stops partway through the array. At a flat 768-token
# budget a 16-item batch regularly stopped after 5-9 items. Scale the budget
# with batch size instead of guessing one constant for every size.
CLASSIFICATION_TOKENS_PER_FILM = 175
CLASSIFICATION_TOKENS_OVERHEAD = 160
CLASSIFICATION_MIN_TOKENS = 256
UNCATEGORIZED_IMPORTS = "UNCATEGORIZED IMPORTS"
STRICT_SCHEMA_MODELS = {
    "openai/gpt-oss-20b",
    "openai/gpt-oss-120b",
}
# Bounded backoff for rate-limited requests. Classification runs many small
# requests in a row, so a transient 429 should be retried rather than
# immediately failing the whole batch.
_RATE_LIMIT_MAX_ATTEMPTS = 4
_RATE_LIMIT_BASE_SECONDS = 1.5
_RATE_LIMIT_MAX_SECONDS = 20.0


def _classification_max_tokens(count: int) -> int:
    """Scale the completion budget to the batch size being requested."""
    return max(
        CLASSIFICATION_MIN_TOKENS,
        count * CLASSIFICATION_TOKENS_PER_FILM + CLASSIFICATION_TOKENS_OVERHEAD,
    )


def _client(api_key: str) -> Groq:
    return Groq(api_key=api_key)


def _is_rate_limited(message: str) -> bool:
    lowered = message.casefold()
    return "429" in message or "rate_limit" in lowered or "too many requests" in lowered


def _retry_after_seconds(message: str, attempt: int) -> float:
    """Prefer a provider-supplied Retry-After value; otherwise back off."""
    match = re.search(r"retry.?after[\"':\s]+(\d+(?:\.\d+)?)", message, re.IGNORECASE)
    if match:
        return min(float(match.group(1)), _RATE_LIMIT_MAX_SECONDS)
    return min(_RATE_LIMIT_BASE_SECONDS * (2 ** attempt), _RATE_LIMIT_MAX_SECONDS)


def _chat(
    client: Groq,
    model: str,
    system: str,
    user: str,
    max_tokens: int = 1024,
    json_mode: bool = False,
    response_schema: Optional[dict[str, Any]] = None,
) -> str:
    """Make one bounded Groq request and require a usable final answer.

    GPT-OSS models can return their reasoning separately from the final answer.
    In the old request shape, that reasoning was allowed to consume the whole
    small completion budget, leaving ``message.content`` empty.  Grimwatch needs
    the final answer, not a reasoning trace, so explicitly disable it.
    """
    request = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        # ``max_completion_tokens`` is the current Groq/OpenAI-compatible
        # parameter. It applies to the entire generated completion.
        "max_completion_tokens": max_tokens,
        "temperature": 0.2,
    }
    if model.casefold().startswith("openai/gpt-oss-"):
        # GPT-OSS supports low/medium/high reasoning effort. Low is ample
        # for selecting an ID or emitting a short JSON object, and avoids
        # exhausting a small completion on internal reasoning.
        request["include_reasoning"] = False
        request["reasoning_effort"] = "low"
    if response_schema is not None:
        request["response_format"] = {"type": "json_schema", "json_schema": response_schema}
    elif json_mode:
        request["response_format"] = {"type": "json_object"}

    resp = None
    last_exc: Optional[Exception] = None
    for attempt in range(_RATE_LIMIT_MAX_ATTEMPTS):
        try:
            resp = client.chat.completions.create(**request)
            break
        except Exception as exc:
            log_exception(LOGGER, f"Groq request failed on attempt {attempt + 1}", exc)
            message = str(exc)
            if "413" in message or "Request too large" in message:
                raise RuntimeError(
                    "Groq rejected the request as too large; the archive context was reduced. Try again shortly."
                ) from exc
            if not _is_rate_limited(message) or attempt == _RATE_LIMIT_MAX_ATTEMPTS - 1:
                if _is_rate_limited(message):
                    raise RuntimeError(
                        f"Groq is rate-limiting requests; gave up after {attempt + 1} attempt(s): {exc}"
                    ) from exc
                raise
            last_exc = exc
            time.sleep(_retry_after_seconds(message, attempt))
    if resp is None:  # pragma: no cover - defensive; loop always returns or raises
        raise RuntimeError("Groq rate-limited the request repeatedly.") from last_exc
    try:
        choice = resp.choices[0]
        message = choice.message
        content = message.content
    except (AttributeError, IndexError) as exc:
        raise RuntimeError("Groq returned no completion choices.") from exc
    if not isinstance(content, str) or not content.strip():
        # Keep the useful provider metadata in the error rather than hiding
        # the underlying issue behind a generic "empty completion" message.
        finish_reason = getattr(choice, "finish_reason", None)
        reasoning = getattr(message, "reasoning", None)
        detail = []
        if finish_reason:
            detail.append(f"finish_reason={finish_reason}")
        if reasoning:
            detail.append("reasoning present but no final text")
        suffix = f" ({'; '.join(detail)})" if detail else ""
        raise RuntimeError(f"Groq returned no final text{suffix}.")
    return content.strip()


def _parse_json(raw: str) -> Any:
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("AI returned an empty response.")
    raw = re.sub(r"```(?:json)?", "", raw, flags=re.IGNORECASE).strip().strip("`").strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # Models occasionally add one sentence around otherwise valid JSON.
        # Use the first balanced-looking JSON start instead of a greedy regex.
        for start in (i for i in range(len(raw)) if raw[i] == "["):
            try:
                return json.JSONDecoder().raw_decode(raw[start:])[0]
            except json.JSONDecodeError:
                continue
        for start in (i for i in range(len(raw)) if raw[i] == "{"):
            try:
                return json.JSONDecoder().raw_decode(raw[start:])[0]
            except json.JSONDecodeError:
                continue
        # A completion can end midway through an otherwise useful JSON object.
        # Recover fields independently; every caller still validates them against
        # local data before using them.
        recovered: dict[str, Any] = {}
        for key in ("title", "film", "movie", "reason"):
            match = re.search(rf'"{key}"\s*:\s*"([^"\n]+)', raw, re.IGNORECASE)
            if match:
                recovered[key] = match.group(1).strip()
        for key in ("id", "candidate_id", "number", "genre_number"):
            match = re.search(rf'"{key}"\s*:\s*"?(\d+)', raw, re.IGNORECASE)
            if match:
                recovered[key] = int(match.group(1))
        if recovered:
            return recovered
        raise ValueError(f"Could not parse AI response:\n{raw}")


def _title_key(title: str) -> str:
    """Compatibility wrapper around the shared archive title normalizer."""
    return normalized_title_key(title)


def _compact_genre(genre: str) -> str:
    return re.split(r"\s*[—(]", str(genre))[0].strip()


def _sample_movies(movies: Sequence[Movie], limit: int = RECOMMENDATION_CONTEXT_ITEMS) -> list[Movie]:
    """Evenly sample a large archive without exceeding the model context limit."""
    if len(movies) <= limit:
        return list(movies)
    step = (len(movies) - 1) / (limit - 1)
    positions = []
    for index in range(limit):
        position = round(index * step)
        if position not in positions:
            positions.append(position)
    return [movies[position] for position in positions]


def _movie_context(movies: Sequence[Movie]) -> str:
    """Build an ID-addressable, bounded candidate list for recommendation calls."""
    lines: list[str] = []
    used = 0
    for candidate_id, movie in enumerate(movies, 1):
        title = re.sub(r"\s+", " ", str(movie.title).strip())[:180]
        line = f"[{candidate_id}] {title} | shelf: {_compact_genre(movie.genre)}"
        if used + len(line) + 1 > RECOMMENDATION_CONTEXT_CHARS:
            break
        lines.append(line)
        used += len(line) + 1
    return "\n".join(lines) or "- none"


def _safe_parse_json(raw: str) -> Any:
    try:
        return _parse_json(raw)
    except ValueError:
        return None


def _response_items(payload: Any) -> list[dict[str, Any]]:
    """Accept current JSON, legacy arrays, and common wrapper objects."""
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("choices", "suggestions", "recommendations", "results", "films", "picks"):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
        if isinstance(value, dict):
            return [value]
    return [payload]


def _coerce_candidate_id(value: object) -> Optional[int]:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        match = re.search(r"\d+", value)
        return int(match.group(0)) if match else None
    return None


def _movie_from_response_item(
    item: dict[str, Any], candidates: Sequence[Movie], unwatched: Sequence[Movie]
) -> Optional[Movie]:
    for key in ("id", "candidate_id", "number", "index"):
        candidate_id = _coerce_candidate_id(item.get(key))
        if candidate_id is not None and 1 <= candidate_id <= len(candidates):
            return candidates[candidate_id - 1]
    for key in ("title", "film", "movie", "name", "choice", "pick"):
        movie = resolve_movie_reference(item.get(key), unwatched)
        if movie is not None:
            return movie
    return None


def _movie_from_raw_response(raw: str, candidates: Sequence[Movie], unwatched: Sequence[Movie]) -> Optional[Movie]:
    """Last-resort recovery for Markdown, numbering, or non-JSON model output."""
    for match in re.finditer(r"(?:candidate\s*)?(?:id|choice|pick|number)?\s*[:#=]?\s*\[?(\d{1,3})\]?", raw, re.I):
        candidate_id = int(match.group(1))
        if 1 <= candidate_id <= len(candidates):
            return candidates[candidate_id - 1]

    normalized_raw = normalized_title_key(raw)
    matches = [
        movie
        for movie in unwatched
        if len(normalized_title_key(movie.title)) >= 4
        and normalized_title_key(movie.title) in normalized_raw
    ]
    return matches[0] if len(matches) == 1 else None


def _clean_reason(value: object, fallback: str) -> str:
    if not isinstance(value, str):
        return fallback
    reason = " ".join(value.replace("\n", " ").split()).strip("`\" ")
    return reason[:280] if reason else fallback


def _local_preference_score(movie: Movie, preference: str) -> int:
    """Score a movie against a free-text preference using only its shelf metadata.

    Used exclusively as a deterministic no-AI fallback. Matches preference
    words directly against genre text — no hardcoded vocabulary.
    """
    if not preference:
        return 0
    searchable = movie.genre.casefold()
    terms = set(re.findall(r"[\w]{3,}", preference.casefold()))
    return sum(4 for term in terms if term in searchable)


def _mood_shelves_via_model(client: Groq, model: str, mood: str, shelves: list[str]) -> list[str]:
    """Ask the model which shelves match the mood. Returns a subset of shelves.

    Returns an empty list on any failure — callers must treat that as
    'no constraint' and fall back to the full pool.
    """
    shelf_list = "\n".join(f"- {s}" for s in shelves)
    system = (
        "You are a film shelf classifier. Given a mood or vibe description, "
        "return the shelf names from the provided list that best match it. "
        'Return ONLY JSON in this exact shape: {"shelves": ["EXACT SHELF NAME", ...]}. '
        "Return an empty shelves array if nothing matches. "
        "Copy shelf names character-for-character from the list — do not paraphrase. "
        "Be precise: do not include a shelf merely because it shares a broad theme. "
        "Match the specific mood intent. If the mood implies mainstream heterosexual romance, "
        "exclude 'Queer Cinema' shelves unless the mood explicitly requests them."
    )
    user = f'Mood: "{mood}"\n\nAvailable shelves:\n{shelf_list}'
    try:
        raw = _chat(client, model, system, user, max_tokens=256, json_mode=True)
        payload = _safe_parse_json(raw)
        if not isinstance(payload, dict):
            return []
        names = payload.get("shelves", [])
        if not isinstance(names, list):
            return []
        shelf_set = {s.casefold().strip() for s in shelves}
        return [n for n in names if isinstance(n, str) and n.casefold().strip() in shelf_set]
    except Exception:
        return []


def _mood_candidate_pool(
    client: Optional[Groq],
    model: Optional[str],
    movies: Sequence[Movie],
    mood: Optional[str],
) -> list[Movie]:
    """Filter the candidate pool to shelves that match the mood.

    Uses a lightweight model call when a client is available. Falls back to
    the full pool silently if the call fails or returns no matches, so a bad
    mood query never produces an empty recommendation screen.
    """
    if not mood:
        return list(movies)

    if client is not None and model is not None:
        available_shelves = sorted({movie.genre for movie in movies if movie.genre})
        matched_shelves = _mood_shelves_via_model(client, model, mood, available_shelves)
        if matched_shelves:
            matched_set = {s.casefold().strip() for s in matched_shelves}
            filtered = [m for m in movies if m.genre.casefold().strip() in matched_set]
            if filtered:
                return filtered

    return list(movies)


def _stable_fallback_movies(
    movies: Sequence[Movie], used: set[int], count: int, seed: str, preference: str = ""
) -> list[Movie]:
    """Return real unwatched movies deterministically when model output is unusable."""
    pool = [movie for movie in movies if id(movie) not in used]
    if not pool:
        return []
    ordered = sorted(
        pool,
        key=lambda movie: (
            -_local_preference_score(movie, preference),
            hashlib.sha256(f"{seed}|{normalized_title_key(movie.title)}".encode("utf-8")).hexdigest(),
            movie.title.casefold(),
        ),
    )
    return ordered[: min(count, len(ordered))]


def _request_recommendation(
    client: Groq,
    model: str,
    system: str,
    recent: str,
    instruction: str,
    unwatched: Sequence[Movie],
    max_tokens: int,
) -> tuple[str, list[Movie], Optional[str]]:
    """Retry with a smaller candidate set if a provider rejects request size."""
    limits = [RECOMMENDATION_CONTEXT_ITEMS, 60, 30]
    last_error: Optional[str] = None
    for limit in dict.fromkeys(limits):
        candidates = _sample_movies(unwatched, min(limit, len(unwatched)))
        context = _movie_context(candidates)
        user = f"Recently watched: {recent}\n\nCandidates:\n{context}\n\n{instruction}"
        try:
            return _chat(client, model, system, user, max_tokens=max_tokens, json_mode=True), candidates, None
        except Exception as exc:
            last_error = str(exc)
            message = last_error.casefold()
            if len(candidates) > 30 and ("too large" in message or "413" in message or "rate" in message):
                continue
            break
    return "", _sample_movies(unwatched, min(30, len(unwatched))), last_error


def _validated_recommendations(
    raw: str,
    candidates: Sequence[Movie],
    unwatched: Sequence[Movie],
    count: int,
    fallback_seed: str,
    request_error: Optional[str] = None,
    fallback_preference: str = "",
) -> list[dict[str, str]]:
    """Map imperfect AI output back to local unwatched entries, then fill safely."""
    payload = _safe_parse_json(raw) if raw else None
    selected: list[dict[str, str]] = []
    used: set[int] = set()
    for item in _response_items(payload):
        movie = _movie_from_response_item(item, candidates, unwatched)
        if movie is None or id(movie) in used:
            continue
        selected.append(
            {
                "title": movie.title,
                "genre": movie.genre,
                "reason": _clean_reason(item.get("reason"), "A strong match from your unwatched shelf."),
            }
        )
        used.add(id(movie))
        if len(selected) >= count:
            return selected

    if not selected and raw:
        movie = _movie_from_raw_response(raw, candidates, unwatched)
        if movie is not None:
            selected.append(
                {
                    "title": movie.title,
                    "genre": movie.genre,
                    "reason": "Recovered from the model response and verified against your unwatched archive.",
                }
            )
            used.add(id(movie))

    if request_error and fallback_preference:
        fallback_reason = (
            "AI was unavailable, so this was matched locally against your request and genre shelves."
        )
    elif request_error:
        fallback_reason = "Selected locally from your unwatched archive because the AI response was unavailable or incomplete."
    else:
        fallback_reason = "Selected locally from your unwatched archive to complete the recommendation."
    for movie in _stable_fallback_movies(
        unwatched, used, count - len(selected), fallback_seed, preference=fallback_preference
    ):
        selected.append({"title": movie.title, "genre": movie.genre, "reason": fallback_reason})
        used.add(id(movie))
    return selected


# ── ENRICH (add years to bare titles) ─────────────────────────────────────────

ENRICH_SYSTEM = """You are a film database. Given a list of film titles without years, return their release years.
Return ONLY valid JSON array. No prose, no markdown.
Format: [{"title": "original title", "year": 1979}, ...]
If you don't know the year with confidence, use null."""


def enrich_years(api_key: str, model: str, titles: list) -> dict:
    """Returns dict of title -> year (or None)."""
    client = _client(api_key)
    user = "\n".join(f"- {t}" for t in titles)
    raw = _chat(client, model, ENRICH_SYSTEM, user, max_tokens=1024)
    results = _parse_json(raw)
    if not isinstance(results, list):
        raise ValueError("Year lookup returned an invalid response.")
    years = {}
    for result in results:
        if not isinstance(result, dict) or not result.get("title"):
            continue
        year = result.get("year")
        if isinstance(year, str) and year.isdigit():
            year = int(year)
        if not isinstance(year, int) or not 1800 <= year <= 2100:
            year = None
        years[_title_key(result["title"])] = year
    return {title: years.get(_title_key(title)) for title in titles}


# ── CLASSIFY ───────────────────────────────────────────────────────────────────

CLASSIFY_SYSTEM = f"""You are a film classification expert. Assign each supplied screen work to exactly one genre from this list:

{CLASSIFICATION_GENRE_LIST_STR}

Rules:
- Return one JSON object only: {{"classifications": [...]}}.
- Preserve each supplied numeric id exactly. Do not invent titles or ids.
- Pick the single best genre number (1 through {len(GENRES)}).
- Return exactly one classification for every supplied id.
- Include a reason of at most 8 words.
- Include confidence as an integer using this exact scale:
  5 = essentially unambiguous primary shelf; another equally valid genre does not exist
  4 = clearly defensible primary shelf, but one credible alternative exists
  3 = genuinely cross-genre or ambiguous; two or more shelves are roughly equally valid
  2 = classification is arguable but probably not the strongest fit
  1 = probably wrong; flag for human review
  Default to 3 or below whenever a runner_up_genre_number is provided and is a strong alternative.
- Include runner_up_genre_number as the closest alternative genre number, or null when no credible alternative exists. It must differ from genre_number.
- Prefer the more specific genre when two fit.
- Treat the supplied verified metadata as the factual boundary: do not invent, contradict, or add facts.
- The reference examples define taxonomy boundaries; they are not a lookup table for input titles.
- Decide from the supplied factual metadata and the work's primary form, genre, and movement. Never add a title-, country-, language-, studio-, region-, or franchise-specific routing rule, and never force a choice from a single metadata field.
- When a film's primary subject is ideological radicalization, political extremism, or identity-through-violence, prefer PSYCHOLOGICAL & CHAMBER DRAMA or SOCIAL REALISM & POLITICAL DRAMA over COMING-OF-AGE & YOUTH DRAMA even if the protagonist is young. Coming-of-age applies when adolescent identity formation is the central dramatic engine, not merely the protagonist's demographic.
- When a film is a literary adaptation but its primary dramatic substance is war, civil conflict, or the historical legacy of mass violence, prefer WAR CINEMA — Ideological, Moral & Political or EPIC HISTORICAL DRAMA over PRESTIGE LITERARY & HISTORICAL DRAMA. The adaptation origin does not override subject matter.
- When an epic historical film's primary genre energy is adventure and physical propulsion rather than moral inquiry or tragedy, prefer EPIC HISTORICAL DRAMA — Auteur & Art-House only if there is clear auteur vision; otherwise prefer ACTION CINEMA — Crafted & Kinetic or BRITISH IMPERIAL & COLONIAL ADVENTURE CINEMA.

Format:
{{"classifications": [
  {{"id": 1, "genre_number": 1, "confidence": 5, "runner_up_genre_number": null, "reason": "Defining work of this lineage."}}
]}}"""


@dataclass(frozen=True)
class _ClassificationCandidate:
    """A title that has cleared metadata verification for model classification."""

    title: str
    metadata: VerifiedMetadata

    def prompt_row(self, index: int) -> str:
        return f"[{index}] {self.title}\n{self.metadata.prompt_context}"


def _short_error(exc: object, limit: int = 160) -> str:
    """Trim a provider exception to something fit for a table cell.

    Groq's 400 errors embed the full ``failed_generation`` payload (the
    model's entire raw, possibly multi-kilobyte JSON attempt) inside the
    exception text. Left untrimmed, that turns the classify table's WHY
    column into a wall of JSON for every failed row. Keep only the
    human-readable part of the message.
    """
    text = " ".join(str(exc).split())
    text = re.split(r"'failed_generation'", text)[0]
    text = text.rstrip(", '\"{[")
    if len(text) > limit:
        text = text[:limit].rstrip() + "…"
    return text or "unknown error"


def _classification_items(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        entries = payload.get("classifications")
        if isinstance(entries, list):
            return [item for item in entries if isinstance(item, dict)]
        return [payload]
    return []


def _fallback_classification(title: str, reason: str) -> dict[str, Any]:
    return {
        "title": title,
        "genre_header": UNCATEGORIZED_IMPORTS,
        "confidence": None,
        "runner_up_genre_number": None,
        "reason": reason,
        "needs_review": True,
    }


def _classification_schema(count: int) -> dict[str, Any]:
    """Strict Groq schema for one batch's worth of classification records.

    This deliberately has no ``minItems``. Groq's strict structured-output
    mode uses constrained decoding, which cannot exceed the completion's
    token budget: if a batch's ``minItems`` demanded more records than fit
    in that budget, GPT-OSS would stop partway through the array and Groq
    rejected the *entire* response with a 400 ``json_validate_failed``
    before this code ever saw it — turning one slow title into a fully
    failed batch. ``maxItems`` is kept as a sane upper bound; every returned
    record is still validated locally (id range, genre range, one result per
    title) before use, and anything missing is retried in smaller batches.
    """
    return {
        "name": "grimwatch_film_classifications",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "classifications": {
                    "type": "array",
                    "maxItems": count,
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "integer", "minimum": 1, "maximum": count},
                            "genre_number": {"type": "integer", "minimum": 1, "maximum": len(GENRES)},
                            "confidence": {"type": "integer", "minimum": 1, "maximum": 5},
                            "runner_up_genre_number": {
                                "type": ["integer", "null"],
                                "minimum": 1,
                                "maximum": len(GENRES),
                            },
                            "reason": {"type": "string"},
                        },
                        "required": [
                            "id",
                            "genre_number",
                            "confidence",
                            "runner_up_genre_number",
                            "reason",
                        ],
                        "additionalProperties": False,
                    },
                }
            },
            "required": ["classifications"],
            "additionalProperties": False,
        },
    }


def _supports_strict_schema(model: str) -> bool:
    return model.casefold() in STRICT_SCHEMA_MODELS


def _request_classifications(
    client: Groq,
    model: str,
    candidates: Sequence[_ClassificationCandidate],
) -> tuple[dict[int, dict[str, Any]], Optional[str]]:
    """Make one classification request and locally validate whatever comes back.

    Returns the id-indexed matches plus a request-level error string (if the
    request itself failed, e.g. Groq still rejected the batch, rate limits
    were exhausted, or the response could not be parsed at all). A request
    error does not necessarily mean every title failed — the caller decides
    what to do with whatever ids remain unmatched.
    """
    user = "Classify these verified screen works by id:\n" + "\n".join(
        candidate.prompt_row(index) for index, candidate in enumerate(candidates, 1)
    )
    request_options: dict[str, Any] = {"max_tokens": _classification_max_tokens(len(candidates))}
    if _supports_strict_schema(model):
        request_options["response_schema"] = _classification_schema(len(candidates))
    else:
        # Keep the command usable for a user-selected model that only supports
        # JSON Object Mode; local validation and gap retries remain in force.
        request_options["json_mode"] = True

    try:
        raw = _chat(client, model, CLASSIFY_SYSTEM, user, **request_options)
    except Exception as exc:
        return {}, _short_error(exc)

    payload = _safe_parse_json(raw)
    matched: dict[int, dict[str, Any]] = {}
    for item in _classification_items(payload):
        item_id = _coerce_candidate_id(item.get("id"))
        if item_id is None and isinstance(item.get("title"), str):
            title_matches = [
                index
                for index, candidate in enumerate(candidates, 1)
                if titles_equivalent(item["title"], candidate.title)
            ]
            item_id = title_matches[0] if len(title_matches) == 1 else None
        if item_id is None or not 1 <= item_id <= len(candidates) or item_id in matched:
            continue
        try:
            genre_number = int(item.get("genre_number"))
        except (TypeError, ValueError):
            continue
        if not 1 <= genre_number <= len(GENRES):
            continue
        if "confidence" not in item or "runner_up_genre_number" not in item:
            continue
        raw_confidence = item.get("confidence")
        if isinstance(raw_confidence, bool):
            continue
        try:
            confidence = int(raw_confidence)
        except (TypeError, ValueError):
            continue
        if not 1 <= confidence <= 5:
            continue
        raw_runner_up = item.get("runner_up_genre_number")
        if raw_runner_up is None:
            runner_up_genre_number = None
        else:
            if isinstance(raw_runner_up, bool):
                continue
            try:
                runner_up_genre_number = int(raw_runner_up)
            except (TypeError, ValueError):
                continue
            if (
                not 1 <= runner_up_genre_number <= len(GENRES)
                or runner_up_genre_number == genre_number
            ):
                continue
        metadata = candidates[item_id - 1].metadata
        needs_review = bool(getattr(metadata, "needs_review", False))
        reason = _clean_reason(item.get("reason"), "Classified from the imported source.")
        if needs_review:
            metadata_reason = _clean_reason(getattr(metadata, "reason", None), "")
            if metadata_reason:
                reason = _clean_reason(f"Review: {metadata_reason} Model: {reason}", reason)
        matched[item_id] = {
            "title": candidates[item_id - 1].title,
            "genre_header": GENRES[genre_number - 1],
            "confidence": confidence,
            "runner_up_genre_number": runner_up_genre_number,
            "reason": reason,
            "needs_review": needs_review,
        }
    return matched, None


def _classify_batch(
    client: Groq,
    model: str,
    candidates: Sequence[_ClassificationCandidate],
    retry_sizes: Sequence[int] = CLASSIFICATION_RETRY_BATCH_SIZES,
) -> list[dict[str, Any]]:
    """Classify one batch, retrying only the unresolved titles in smaller groups.

    A response that is syntactically valid but incomplete (some titles never
    got a matching id back) used to look successful and silently became
    UNCATEGORIZED. A response that Groq rejects outright (e.g. a strict
    schema violation) used to fail the whole batch at once. Both cases are
    now handled the same way: only the titles still missing after this
    request are retried, at progressively smaller batch sizes, all the way
    down to one at a time. A title is only ever marked as a failure once
    every retry size has been exhausted for it.
    """
    matched, request_error = _request_classifications(client, model, candidates)
    missing_indexes = [index for index in range(1, len(candidates) + 1) if index not in matched]

    results: dict[int, dict[str, Any]] = dict(matched)
    if missing_indexes and retry_sizes:
        retry_size, *remaining_sizes = retry_sizes
        missing_candidates = [candidates[index - 1] for index in missing_indexes]
        replacement_rows: list[dict[str, Any]] = []
        for start in range(0, len(missing_candidates), retry_size):
            replacement_rows.extend(
                _classify_batch(
                    client,
                    model,
                    missing_candidates[start:start + retry_size],
                    retry_sizes=remaining_sizes,
                )
            )
        for index, replacement in zip(missing_indexes, replacement_rows):
            results[index] = replacement
    elif missing_indexes:
        # Every retry size has been tried for these titles; only now is a
        # title actually marked as a failure.
        fallback_reason = (
            f"AI classification unavailable: {request_error}"
            if request_error
            else "AI did not return a valid classification for this title after retries."
        )
        for index in missing_indexes:
            results[index] = _fallback_classification(candidates[index - 1].title, fallback_reason)

    return [results[index] for index in range(1, len(candidates) + 1)]


def classify_movies(
    api_key: str,
    model: str,
    titles: Sequence[str],
    *,
    tmdb_api_key: Optional[str] = None,
    omdb_api_key: Optional[str] = None,
    refresh_metadata: bool = False,
) -> list[dict[str, Any]]:
    """Classify titles only after a global metadata verification pass."""
    clean_titles = [str(title).strip() for title in titles if str(title).strip()]
    if not clean_titles:
        return []
    metadata_options: dict[str, Any] = {}
    if tmdb_api_key:
        metadata_options["tmdb_api_key"] = tmdb_api_key
    if omdb_api_key:
        metadata_options["omdb_api_key"] = omdb_api_key
    if refresh_metadata:
        metadata_options["refresh_metadata"] = True
    try:
        metadata_rows = verify_film_metadata(clean_titles, **metadata_options)
    except Exception as exc:
        log_exception(LOGGER, "Metadata verification raised unexpectedly", exc)
        detail = _short_error(exc)
        return [
            _fallback_classification(title, f"Metadata verification unavailable: {detail}")
            for title in clean_titles
        ]
    if len(metadata_rows) != len(clean_titles):
        return [
            _fallback_classification(title, "Metadata verification returned an incomplete result set.")
            for title in clean_titles
        ]

    verified_candidates: list[tuple[int, _ClassificationCandidate]] = []
    results: list[dict[str, Any] | None] = [None] * len(clean_titles)
    for index, (title, metadata) in enumerate(zip(clean_titles, metadata_rows)):
        if metadata.verified:
            verified_candidates.append((index, _ClassificationCandidate(title, metadata)))
        else:
            results[index] = _fallback_classification(
                title,
                f"Metadata verification required: {metadata.reason}",
            )
    if not verified_candidates:
        return [result for result in results if result is not None]
    try:
        client = _client(api_key)
    except Exception as exc:
        log_exception(LOGGER, "Groq client initialization failed", exc)
        for index, candidate in verified_candidates:
            results[index] = _fallback_classification(
                candidate.title, f"AI classification unavailable: {_short_error(exc)}"
            )
        return [result for result in results if result is not None]

    for start in range(0, len(verified_candidates), CLASSIFICATION_BATCH_SIZE):
        batch = verified_candidates[start : start + CLASSIFICATION_BATCH_SIZE]
        classified = _classify_batch(client, model, [candidate for _, candidate in batch])
        for (index, _candidate), result in zip(batch, classified):
            results[index] = result
    return [result for result in results if result is not None]


# ── SUGGEST ────────────────────────────────────────────────────────────────────

def suggest_movies(
    api_key: str,
    model: str,
    unwatched: Sequence[Movie],
    recently_watched: Sequence[Movie],
    mood: Optional[str] = None,
    similar_to: Optional[str] = None,
    contrast: bool = False,
    count: int = 3,
    contrast_against: Optional[str] = None,
) -> list[dict[str, str]]:
    """Recommend only local unwatched entries, even when the model is messy."""
    requested_count = max(1, min(int(count), 20))
    if not unwatched:
        return []
    try:
        _groq_client = _client(api_key)
    except Exception as exc:
        request_error = str(exc)
        _groq_client = None
    selection_pool = _mood_candidate_pool(_groq_client, model, unwatched, mood)
    recent = ", ".join(movie.title for movie in recently_watched[-10:]) or "none yet"
    recent = recent[:1800]
    if contrast_against:
        instruction = (
            f"Pick {requested_count} films that sharply contrast with '{contrast_against}' "
            f"in genre, tone, pace, and era. Avoid anything from the same shelf."
        )
    elif mood:
        instruction = (
            f'The user wants: "{mood}". Pick {requested_count} best matches from the candidates. '
            f"Stay strictly within the mood's genre intent — do not pick titles from shelves "
            f"that only loosely match the mood description."
        )
    elif similar_to:
        instruction = f'Pick {requested_count} films similar in theme or style to "{similar_to}".'
    elif contrast:
        instruction = f"Pick {requested_count} films that sharply contrast with the recent watches."
    else:
        instruction = f"Pick {requested_count} films the user should watch next based on history and list balance."
    instruction += (
        " Return ONLY JSON: {\"choices\":[{\"id\":17,\"reason\":\"brief reason\"}]}. "
        "Use only the numeric ids shown in Candidates."
    )
    system = (
        "You are a discerning film recommender. Select only candidate IDs from the user's "
        "unwatched archive. Do not invent titles, genres, or IDs. Reasons must not invent "
        "specific cast, character, plot, or demographic details; only use the supplied shelf "
        "metadata and the user's stated preference."
    )

    raw = ""
    candidates = _sample_movies(selection_pool)
    request_error = None
    try:
        if _groq_client is None:
            _groq_client = _client(api_key)
        raw, candidates, request_error = _request_recommendation(
            _groq_client, model, system, recent, instruction, selection_pool, max_tokens=768
        )
    except Exception as exc:
        request_error = str(exc)
    seed = f"suggest|{mood or ''}|{similar_to or ''}|{contrast_against or ''}|{contrast}|{recent}"
    return _validated_recommendations(
        raw,
        candidates,
        selection_pool,
        requested_count,
        seed,
        request_error=request_error,
        fallback_preference=contrast_against or mood or similar_to or ("contrast" if contrast else ""),
    )


def suggest_external_movies(
    api_key: str,
    model: str,
    recently_watched: Sequence[Movie],
    unwatched_titles: Sequence[str],
    mood: Optional[str] = None,
    similar_to: Optional[str] = None,
    count: int = 3,
) -> list[dict[str, str]]:
    """Suggest verified-shaped films outside the user's current archive."""
    requested_count = max(1, min(int(count), 20))
    archive_keys = {
        normalized_title_key(title)
        for title in (*unwatched_titles, *(movie.title for movie in recently_watched))
        if normalized_title_key(title)
    }
    recent_titles = ", ".join(movie.title for movie in recently_watched[-10:]) or "none yet"
    archive_sample = ", ".join(str(title) for title in unwatched_titles[:100])[:4000] or "none"
    request = mood or similar_to or "films that complement the recent watches"
    system = (
        "You are a film expert. Suggest real films NOT in the user's archive that fit their request. "
        'Return ONLY JSON: {"suggestions": [{"title": "Film Title (YEAR)", '
        '"genre_number": 1, "reason": "brief reason"}]}. '
        f"Use one genre_number from this taxonomy:\n{GENRE_LIST_STR}\n"
        "Do not suggest any title that appears in the user's unwatched list or recently watched list. "
        "Only suggest real films with verifiable release years. Reasons must not invent specific "
        "cast, character, plot, or demographic details."
    )
    user = (
        f"Recently watched: {recent_titles}\n"
        f"Already in archive: {archive_sample}\n"
        f"Request: {request}\n"
        f"Suggest {requested_count} films."
    )

    try:
        raw = _chat(_client(api_key), model, system, user, max_tokens=1024, json_mode=True)
    except Exception:
        return []

    payload = _safe_parse_json(raw)
    suggestions = _response_items(payload)
    results: list[dict[str, str]] = []
    used_keys: set[str] = set()
    for suggestion in suggestions:
        title = suggestion.get("title")
        reason = suggestion.get("reason")
        genre_number = _coerce_candidate_id(suggestion.get("genre_number"))
        if not all(isinstance(value, str) for value in (title, reason)):
            continue
        title = clean_display_title(title)
        title_key = normalized_title_key(title)
        if (
            not title_key
            or extract_title_year(title) is None
            or title_key in archive_keys
            or title_key in used_keys
            or genre_number is None
            or not 1 <= genre_number <= len(GENRES)
        ):
            continue
        results.append({"title": title, "genre": GENRES[genre_number - 1], "reason": reason.strip()})
        used_keys.add(title_key)
        if len(results) == requested_count:
            break
    return results


# ── TONIGHT ────────────────────────────────────────────────────────────────────

def suggest_tonight(
    api_key: str,
    model: str,
    unwatched: Sequence[Movie],
    recently_watched: Sequence[Movie],
    max_runtime: Optional[int] = None,
    genre: Optional[str] = None,
) -> dict[str, str]:
    """Choose a verified local unwatched movie; model failure gets a safe fallback."""
    if not unwatched:
        raise ValueError("There are no unwatched films to choose from.")
    recent = ", ".join(movie.title for movie in recently_watched[-5:]) or "none"
    runtime_note = (
        f"The user prefers a film that is likely under {max_runtime} minutes. "
        "Treat this as a preference, not verified runtime data. "
        if max_runtime
        else ""
    )
    genre_note = f'Limit the choice to the shelf or genre "{genre}". ' if genre else ""
    instruction = (
        f"{runtime_note}{genre_note}Pick exactly one. Return ONLY JSON: "
        '{"id":17,"reason":"brief reason"}. Use only an id shown in Candidates.'
    )
    system = (
        "You are choosing one film for tonight. Select only an ID from the supplied "
        "unwatched candidates; never invent a title, genre, or ID."
    )
    raw = ""
    candidates = _sample_movies(unwatched)
    request_error: Optional[str] = None
    try:
        client = _client(api_key)
        raw, candidates, request_error = _request_recommendation(
            client, model, system, recent[:1200], instruction, unwatched, max_tokens=640
        )
    except Exception as exc:
        request_error = str(exc)
    seed = f"tonight|{max_runtime or ''}|{genre or ''}|{recent}"
    return _validated_recommendations(
        raw, candidates, unwatched, 1, seed, request_error=request_error
    )[0]


# ── BLIND SPOTS ────────────────────────────────────────────────────────────────

BLIND_SPOT_SHELF_CONTEXT_CHARS = 3600
BLIND_SPOT_WATCHED_CONTEXT_CHARS = 1800


def _blind_spot_candidates(genre_stats: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Normalize and rank shelves so the smallest coverage gaps are considered first."""
    candidates: list[dict[str, Any]] = []
    for stat in genre_stats:
        if not isinstance(stat, dict) or not isinstance(stat.get("genre"), str):
            continue
        try:
            total = int(stat.get("total", 0))
            watched_count = int(stat.get("watched", 0))
        except (TypeError, ValueError):
            continue
        if total <= 0:
            continue
        candidates.append(
            {
                "genre": stat["genre"],
                "watched": max(0, min(watched_count, total)),
                "total": total,
            }
        )
    return sorted(
        candidates,
        key=lambda stat: (stat["watched"] / stat["total"], -stat["total"], stat["genre"].casefold()),
    )


def _blind_spot_gap(stat: dict[str, Any], reason: str) -> dict[str, Any]:
    return {
        "genre": stat["genre"],
        "watched": stat["watched"],
        "total": stat["total"],
        "coverage_percent": stat["watched"] / stat["total"] * 100,
        "reason": reason,
    }


def find_blind_spots(
    api_key: str, model: str, watched: Sequence[Movie], genre_stats: Sequence[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Return compact model-selected shelf gaps, with a local coverage fallback."""
    candidates = _blind_spot_candidates(genre_stats)
    if not candidates:
        return []
    client = _client(api_key)
    shelf_by_id: dict[int, dict[str, Any]] = {}
    shelf_lines: list[str] = []
    shelf_chars = 0
    for shelf_id, stat in enumerate(candidates, 1):
        label = " ".join(str(stat["genre"]).split())[:96]
        line = f"[{shelf_id}] {label}: {stat['watched']}/{stat['total']} watched"
        if shelf_lines and shelf_chars + len(line) + 1 > BLIND_SPOT_SHELF_CONTEXT_CHARS:
            break
        shelf_lines.append(line)
        shelf_by_id[shelf_id] = stat
        shelf_chars += len(line) + 1
    watched_lines: list[str] = []
    watched_chars = 0
    for movie in watched[-25:]:
        line = f"- {str(movie.title).strip()[:140]}"
        if watched_lines and watched_chars + len(line) + 1 > BLIND_SPOT_WATCHED_CONTEXT_CHARS:
            break
        watched_lines.append(line)
        watched_chars += len(line) + 1
    system = (
        "You are a film critic. Identify up to three meaningful blind spots from the supplied shelves. "
        'Return ONLY JSON: {"gaps":[{"shelf_id":1,"reason":"brief reason"}]}. '
        "Use only an integer shelf_id from Candidate shelves."
    )
    watched_history = "\n".join(watched_lines) or "- none"
    shelf_summary = "\n".join(shelf_lines)
    user = (
        f"Recent watched titles (context only):\n{watched_history}\n\n"
        f"Candidate shelves:\n{shelf_summary}\n\n"
        "Identify the most useful blind spots."
    )
    try:
        raw = _chat(client, model, system, user, max_tokens=512, json_mode=True)
    except Exception as exc:
        if "too large" in str(exc).casefold() or "413" in str(exc):
            return [_blind_spot_gap(stat, "One of the least-watched shelves in your archive.") for stat in candidates[:3]]
        raise
    payload = _safe_parse_json(raw)
    if not isinstance(payload, dict) or not isinstance(payload.get("gaps"), list):
        return [_blind_spot_gap(stat, "One of the least-watched shelves in your archive.") for stat in candidates[:3]]
    gaps: list[dict[str, Any]] = []
    selected: set[int] = set()
    for item in payload["gaps"]:
        if not isinstance(item, dict):
            continue
        shelf_id = _coerce_candidate_id(item.get("shelf_id"))
        if shelf_id is None or shelf_id in selected:
            continue
        stat = shelf_by_id.get(shelf_id)
        if stat is None:
            continue
        gaps.append(_blind_spot_gap(stat, str(item.get("reason", "")).strip()))
        selected.add(shelf_id)
        if len(gaps) == 3:
            break
    return gaps or [_blind_spot_gap(stat, "One of the least-watched shelves in your archive.") for stat in candidates[:3]]


# ── AUDIT ──────────────────────────────────────────────────────────────────────

AUDIT_SYSTEM = f"""You are a strict film classification auditor.
Given a film and its current genre, check if it's correctly classified against this list:

{GENRE_LIST_STR}

Return ONLY valid JSON:
{{"correct": true/false, "suggested_genre_number": <number or null>, "reason": "one sentence"}}"""


def audit_classification(api_key, model, title, current_genre) -> dict:
    client = _client(api_key)
    user = f'Film: "{title}"\nCurrent genre: "{current_genre}"\nCorrect?'
    raw = _chat(client, model, AUDIT_SYSTEM, user, max_tokens=256, json_mode=True)
    result = _parse_json(raw)
    if not result.get("correct") and result.get("suggested_genre_number"):
        idx = int(result["suggested_genre_number"]) - 1
        idx = max(0, min(idx, len(GENRES) - 1))
        result["suggested_genre"] = GENRES[idx]
    return result

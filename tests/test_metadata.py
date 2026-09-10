import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from grimwatch.metadata import (
    MetadataCache,
    MetadataProviderError,
    VerifiedMetadata,
    WikidataMetadataVerifier,
    _Candidate,
    _ProviderResult,
)
from grimwatch.parser import extract_title_year


def _item(label, *, description="", claims=None):
    return {
        "labels": {"en": {"value": label}},
        "aliases": {},
        "descriptions": {"en": {"value": description}},
        "claims": claims or {},
    }


def _entity_claim(property_id):
    return {"mainsnak": {"datavalue": {"value": {"id": property_id}}}}


def _time_claim(year):
    return {"mainsnak": {"datavalue": {"value": {"time": f"+{year}-01-01T00:00:00Z"}}}}


class WikidataMetadataVerifierTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.cache_path = Path(self.temp_dir.name) / "metadata_cache.sqlite"

    def tearDown(self):
        self.temp_dir.cleanup()

    def _verifier(self, **kwargs):
        return WikidataMetadataVerifier(cache_path=self.cache_path, **kwargs)

    @staticmethod
    def _record(title, *, source="Wikidata", fuzzy=False):
        return VerifiedMetadata(
            title=title,
            verified=True,
            reason=f"Verified against {source} ({'fuzzy' if fuzzy else 'exact'} match).",
            release_year=extract_title_year(title),
            media_types=("film",),
            source=source,
            source_id="test-id",
            match_method="fuzzy" if fuzzy else "exact",
            match_confidence=0.94 if fuzzy else 1.0,
            needs_review=fuzzy,
        )

    def test_large_batch_uses_one_query_instead_of_per_title_searches(self):
        verifier = self._verifier()
        titles = [f"Film {index} (2000)" for index in range(1, 6)]
        rows = [
            {
                "input": {"value": str(index)},
                "item": {"value": f"http://www.wikidata.org/entity/Q{index}"},
                "date": {"value": "+2000-01-01T00:00:00Z"},
                "typeLabel": {"value": "film"},
            }
            for index in range(5)
        ]

        with patch.object(verifier, "_sparql", return_value=rows) as query, patch.object(
            verifier, "_request"
        ) as action_api:
            records = verifier.verify(titles)

        query.assert_called_once()
        action_api.assert_not_called()
        self.assertTrue(all(record.verified for record in records))
        self.assertEqual(titles, [record.title for record in records])

    def test_year_and_screen_work_disambiguate_identical_titles(self):
        verifier = self._verifier()
        primary = {
            "Qfilm": _item(
                "Galaxy Quest",
                description="1999 science-fiction comedy film",
                claims={
                    "P31": [_entity_claim("Qfilmtype")],
                    "P577": [_time_claim(1999)],
                    "P495": [_entity_claim("Qcountry")],
                    "P57": [_entity_claim("Qdirector")],
                    "P136": [_entity_claim("Qgenre")],
                    "P161": [_entity_claim("Qactor")],
                },
            ),
            "Qcomic": _item(
                "Galaxy Quest",
                description="1999 comic book series",
                claims={"P31": [_entity_claim("QcomicType")], "P577": [_time_claim(1999)]},
            ),
        }
        references = {
            "Qfilmtype": _item("film"),
            "Qcountry": _item("United States"),
            "Qdirector": _item("Dean Parisot"),
            "Qgenre": _item("science fiction comedy"),
            "Qactor": _item("Tim Allen"),
            "QcomicType": _item("comic book series"),
        }

        def fake_request(params):
            if params["action"] == "wbsearchentities":
                return {
                    "search": [
                        {"id": "Qfilm", "label": "Galaxy Quest", "description": "1999 science-fiction comedy film"},
                        {"id": "Qcomic", "label": "Galaxy Quest", "description": "1999 comic book series"},
                    ]
                }
            ids = params["ids"].split("|")
            table = primary if "Qfilm" in ids else references
            return {"entities": {item_id: table[item_id] for item_id in ids}}

        with patch.object(verifier, "_request", side_effect=fake_request):
            record = verifier.verify(["Galaxy Quest (1999)"])[0]

        self.assertTrue(record.verified)
        self.assertEqual(1999, record.release_year)
        self.assertEqual(("United States",), record.countries)
        self.assertEqual(("Dean Parisot",), record.directors)
        self.assertEqual(("Tim Allen",), record.cast)
        self.assertIn("science fiction comedy", record.prompt_context)
        self.assertIn("Tim Allen", record.prompt_context)

    def test_year_mismatch_requires_review_instead_of_guessing(self):
        verifier = self._verifier()

        def fake_request(params):
            if params["action"] == "wbsearchentities":
                return {"search": [{"id": "Qwork", "label": "The Voyage of Charles Darwin"}]}
            return {
                "entities": {
                    "Qwork": _item(
                        "The Voyage of Charles Darwin",
                        description="television miniseries",
                        claims={"P31": [_entity_claim("Qtype")], "P577": [_time_claim(1979)]},
                    ),
                    "Qtype": _item("television miniseries"),
                }
            }

        with patch.object(verifier, "_request", side_effect=fake_request):
            record = verifier.verify(["The Voyage of Charles Darwin (1978)"])[0]

        self.assertFalse(record.verified)
        self.assertIn("1978", record.reason)

    def test_cache_hit_reuses_successful_metadata_without_any_network_call(self):
        title = "Cache Film (2000)"
        primary = {
            "Qfilm": _item(
                "Cache Film",
                claims={"P31": [_entity_claim("Qtype")], "P577": [_time_claim(2000)]},
            )
        }
        references = {"Qtype": _item("film")}

        def fake_request(params):
            if params["action"] == "wbsearchentities":
                return {"search": [{"id": "Qfilm", "label": "Cache Film"}]}
            ids = params["ids"].split("|")
            table = primary if "Qfilm" in ids else references
            return {"entities": {item_id: table[item_id] for item_id in ids}}

        first = self._verifier()
        with patch.object(first, "_request", side_effect=fake_request):
            fetched = first.verify([title])[0]

        second = self._verifier()
        with patch.object(second, "_request") as request:
            cached = second.verify([title])[0]

        self.assertTrue(fetched.verified)
        self.assertTrue(cached.verified)
        self.assertTrue(cached.from_cache)
        self.assertIn("local cache", cached.reason)
        request.assert_not_called()

    def test_cache_expiry_and_refresh_bypass_the_cache(self):
        cache = MetadataCache(self.cache_path, ttl_seconds=10)
        record = self._record("Cache Film (2000)")
        cache.put("cache film", 2000, record, now=100.0)
        self.assertIsNotNone(cache.get("cache film", 2000, now=110.0))
        self.assertIsNone(cache.get("cache film", 2000, now=111.0))

        verifier = self._verifier()
        verifier._cache.put("cache film", 2000, record, now=time.time())
        refreshed = self._record("Cache Film (2000)", source="TMDb")
        with patch.object(
            verifier,
            "_resolve_wikidata_rest",
            return_value=_ProviderResult.resolved(refreshed),
        ) as resolver:
            result = verifier.verify(["Cache Film (2000)"], refresh_metadata=True)[0]

        resolver.assert_called_once_with("Cache Film (2000)")
        self.assertEqual("TMDb", result.source)
        self.assertFalse(result.from_cache)

    def test_cache_round_trips_cast(self):
        cache = MetadataCache(self.cache_path)
        record = VerifiedMetadata(
            title="Cast Cache (2000)",
            verified=True,
            reason="Test metadata.",
            cast=("Example Actor",),
        )

        cache.put("cast cache", 2000, record, now=100.0)
        cached = cache.get("cast cache", 2000, now=101.0)

        self.assertIsNotNone(cached)
        self.assertEqual(("Example Actor",), cached.cast)

    def test_sparql_failure_falls_through_to_rest_for_every_title(self):
        verifier = self._verifier()
        titles = [f"Film {index} (2000)" for index in range(1, 6)]

        with patch.object(
            verifier,
            "_sparql",
            side_effect=MetadataProviderError("Wikidata", "rate_limited", "HTTP 429"),
        ), patch.object(
            verifier,
            "_resolve_wikidata_rest",
            side_effect=lambda title: _ProviderResult.resolved(self._record(title)),
        ) as rest:
            records = verifier.verify(titles)

        self.assertEqual(5, rest.call_count)
        self.assertTrue(all(record.verified for record in records))
        self.assertTrue(all(record.source == "Wikidata" for record in records))

    def test_fuzzy_match_is_verified_but_requires_review(self):
        verifier = self._verifier()
        primary = {
            "Qfilm": _item(
                "Adventures of Example, The",
                claims={"P31": [_entity_claim("Qtype")], "P577": [_time_claim(2000)]},
            )
        }
        references = {"Qtype": _item("film")}

        def fake_request(params):
            if params["action"] == "wbsearchentities":
                return {"search": [{"id": "Qfilm", "label": "Adventures of Example, The"}]}
            ids = params["ids"].split("|")
            table = primary if "Qfilm" in ids else references
            return {"entities": {item_id: table[item_id] for item_id in ids}}

        with patch.object(verifier, "_request", side_effect=fake_request):
            record = verifier.verify(["The Adventures of Example (2000)"])[0]

        self.assertTrue(record.verified)
        self.assertTrue(record.needs_review)
        self.assertEqual("fuzzy", record.match_method)
        self.assertGreaterEqual(record.match_confidence, 0.90)
        self.assertIn("review recommended", record.reason)

    def test_fuzzy_near_tie_is_not_selected(self):
        verifier = self._verifier()
        candidates = [
            _Candidate("one", ("Last Example The",), 2000, {}),
            _Candidate("two", ("Example The Last",), 2000, {}),
        ]

        self.assertIsNone(verifier._choose_candidate("The Last Example", candidates))

    def test_optional_sources_are_ordered_and_stop_after_success(self):
        verifier = self._verifier(tmdb_api_key="tmdb", omdb_api_key="omdb")
        tmdb_record = self._record("Fallback Film (2000)", source="TMDb")

        with patch.object(verifier, "_resolve_wikidata_rest", return_value=_ProviderResult(status="no_match")) as wikidata, patch.object(
            verifier, "_resolve_tmdb", return_value=_ProviderResult.resolved(tmdb_record)
        ) as tmdb, patch.object(verifier, "_resolve_omdb") as omdb:
            record = verifier.verify(["Fallback Film (2000)"])[0]

        wikidata.assert_called_once()
        tmdb.assert_called_once()
        omdb.assert_not_called()
        self.assertEqual("TMDb", record.source)

    def test_omdb_runs_only_after_configured_tmdb_returns_no_match(self):
        verifier = self._verifier(tmdb_api_key="tmdb", omdb_api_key="omdb")
        omdb_record = self._record("Last Resort (2000)", source="OMDb")

        with patch.object(verifier, "_resolve_wikidata_rest", return_value=_ProviderResult(status="no_match")), patch.object(
            verifier, "_resolve_tmdb", return_value=_ProviderResult(status="no_match")
        ) as tmdb, patch.object(
            verifier, "_resolve_omdb", return_value=_ProviderResult.resolved(omdb_record)
        ) as omdb:
            record = verifier.verify(["Last Resort (2000)"])[0]

        tmdb.assert_called_once()
        omdb.assert_called_once()
        self.assertEqual("OMDb", record.source)

    def test_tmdb_title_year_lookup_has_no_region_or_language_parameters(self):
        verifier = self._verifier(tmdb_api_key="tmdb")
        calls = []

        def fake_json_request(url, params, **_kwargs):
            calls.append((url, params))
            if url.endswith("/search/movie"):
                return {"results": [{"id": 7, "title": "Example Film", "release_date": "2000-01-01"}]}
            if url.endswith("/search/tv"):
                return {"results": []}
            if url.endswith("/movie/7"):
                return {
                    "release_date": "2000-01-01",
                    "overview": "A test film.",
                    "production_countries": [],
                    "production_companies": [],
                    "genres": [],
                    "credits": {"crew": []},
                }
            self.fail(f"Unexpected TMDb path: {url}")

        with patch.object(verifier, "_json_request", side_effect=fake_json_request):
            record = verifier._resolve_tmdb("Example Film (2000)").record

        self.assertIsNotNone(record)
        self.assertEqual("TMDb", record.source)
        self.assertTrue(all("region" not in params and "language" not in params for _, params in calls))

    def test_review_reasons_distinguish_no_match_rate_limit_and_unreachable(self):
        verifier = self._verifier()
        no_match = verifier._final_review("Unknown Film (2000)", [_ProviderResult(status="no_match")])
        rate_limited = verifier._final_review("Unknown Film (2000)", [_ProviderResult(status="rate_limited")])
        unreachable = verifier._final_review("Unknown Film (2000)", [_ProviderResult(status="unreachable")])
        reachable_no_match = verifier._final_review(
            "Unknown Film (2000)",
            [_ProviderResult(status="rate_limited"), _ProviderResult(status="no_match")],
        )

        self.assertEqual("no_match", no_match.status)
        self.assertIn("spelling or year", no_match.reason)
        self.assertEqual("rate_limited", rate_limited.status)
        self.assertIn("retry", rate_limited.reason)
        self.assertEqual("unreachable", unreachable.status)
        self.assertIn("connection", unreachable.reason)
        self.assertEqual("no_match", reachable_no_match.status)

    def test_fallback_worker_count_is_bounded(self):
        self.assertEqual(4, self._verifier(max_workers=99).max_workers)


if __name__ == "__main__":
    unittest.main()

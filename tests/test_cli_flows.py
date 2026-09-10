import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

try:
    import rich  # noqa: F401
    import typer  # noqa: F401
    from grimwatch import cli
    from grimwatch.commands import add as add_command
    from grimwatch.commands import classify as classify_command
    from grimwatch.commands import dedup as dedup_command
    from grimwatch.commands import export as export_command
    from grimwatch.commands import suggest as suggest_command
    from grimwatch.parser import Movie, MovieList

    HAS_CLI_DEPS = True
except ModuleNotFoundError:
    HAS_CLI_DEPS = False


@unittest.skipUnless(HAS_CLI_DEPS, "requires the package's CLI dependencies")
class CliFlowTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.config_dir = self.root / "config"
        self.archive = self.root / "movies.md"
        self.archive.write_text(
            "CRIME\n\n- [ ] L.A. Confidential (1997)\n",
            encoding="utf-8",
        )
        self.config_dir.mkdir()
        (self.config_dir / "config.json").write_text(
            json.dumps({"list_path": str(self.archive), "groq_model": "test-model"}),
            encoding="utf-8",
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def _environment(self):
        environment = os.environ.copy()
        environment["GRIMWATCH_CONFIG_DIR"] = str(self.config_dir)
        return environment

    def test_merge_command_imports_plain_markdown_and_writes_real_entries(self):
        source = self.root / "incoming.md"
        source.write_text("LA Confidential (1997)\nHeat (1995)\n", encoding="utf-8")

        completed = subprocess.run(
            [sys.executable, "-m", "grimwatch.cli", "merge", f'"{source}"', "--yes"],
            cwd=Path(__file__).parents[1],
            env=self._environment(),
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(0, completed.returncode, completed.stderr + completed.stdout)
        self.assertIn("New films to add:     1", completed.stdout)
        self.assertIn("Already present:      1", completed.stdout)
        saved = self.archive.read_text(encoding="utf-8")
        self.assertIn("Heat (1995)", saved)
        self.assertIn("UNCATEGORIZED IMPORTS", saved)

    def test_dedup_dry_run_reports_normalized_duplicates_without_writing(self):
        self.archive.write_text(
            "CRIME\n\n- [ ] L.A. Confidential (1997)\n- [x] LA Confidential (1997)\n",
            encoding="utf-8",
        )
        before = self.archive.read_text(encoding="utf-8")

        completed = subprocess.run(
            [sys.executable, "-m", "grimwatch.cli", "dedup", "--dry-run"],
            cwd=Path(__file__).parents[1],
            env=self._environment(),
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(0, completed.returncode, completed.stderr + completed.stdout)
        self.assertIn("safe duplicate group", completed.stdout)
        self.assertIn("Dry run", completed.stdout)
        self.assertEqual(before, self.archive.read_text(encoding="utf-8"))

    def test_dedup_prompts_for_the_specific_copy_to_keep(self):
        self.archive.write_text(
            "AUTEUR\n\n- [ ] Aguirre, the Wrath of God (1972)\n\n"
            "HISTORICAL\n\n- [x] Aguirre, the Wrath of God (1972)\n",
            encoding="utf-8",
        )
        config = {"list_path": str(self.archive)}

        with patch.object(dedup_command, "get_config", return_value=config), patch.object(
            dedup_command.Prompt, "ask", return_value="2"
        ) as choose_keep, patch.object(dedup_command.Confirm, "ask", return_value=True):
            dedup_command.run_dedup(yes=False)

        choose_keep.assert_called_once()
        saved = self.archive.read_text(encoding="utf-8")
        self.assertEqual(1, saved.count("Aguirre, the Wrath of God (1972)"))
        self.assertIn("HISTORICAL\n\n- [x] Aguirre, the Wrath of God (1972)", saved)


    def test_recently_shown_titles_are_excluded_from_the_next_recommendation_pool(self):
        archive_id = str(self.archive.resolve()).casefold()
        cfg = {
            "list_path": str(self.archive),
            "recommendation_history": [{"archive": archive_id, "title": "Whiplash (2014)"}],
        }
        movies = [
            Movie("Whiplash (2014)", False, "DRAMA", ""),
            Movie("Persona (1966)", False, "AUTEUR", ""),
        ]

        pool, excluded = suggest_command._recommendation_pool(movies, cfg)

        self.assertEqual(1, excluded)
        self.assertEqual(["Persona (1966)"], [movie.title for movie in pool])

    def test_interactive_suggest_includes_count_and_list_offers_unwatched_filter(self):
        with patch.object(cli.Prompt, "ask", side_effect=["noir", "", "4"]):
            self.assertEqual(
                ["suggest", "--count", "4", "--mood", "noir"],
                cli._guided_command("suggest"),
            )
        with patch.object(cli.Prompt, "ask", side_effect=["", "Stalker (1979)", "2"]):
            self.assertEqual(
                ["suggest", "--count", "2", "--contrast-against", "Stalker (1979)"],
                cli._guided_command("suggest"),
            )
        with patch.object(cli.Prompt, "ask", return_value="crime"), patch.object(
            cli.Confirm, "ask", return_value=True
        ):
            self.assertEqual(
                ["list", "--genre", "crime", "--unwatched"],
                cli._guided_command("list"),
            )

    def test_export_writes_a_portable_markdown_table(self):
        self.archive.write_text(
            "CRIME\n\n- [x] L.A. Confidential (1997)\n- [ ] The Third Man (1949)\n",
            encoding="utf-8",
        )
        output = self.root / "backup.md"

        with patch.object(export_command, "get_config", return_value={"list_path": str(self.archive)}):
            export_command.run_export(str(output))

        exported = output.read_text(encoding="utf-8")
        self.assertIn("| Status | Title | Shelf | Year | Watched On |", exported)
        self.assertIn("| Watched | L.A. Confidential (1997) | CRIME | 1997 |  |", exported)
        self.assertIn("| Unwatched | The Third Man (1949) | CRIME | 1949 |  |", exported)

    def test_import_restores_an_exported_archive_without_ai_classification(self):
        backup = self.root / "export.md"
        backup.write_text(
            "| Status | Title | Shelf | Year | Watched On |\n"
            "| --- | --- | --- | --- | --- |\n"
            "| Watched | Le Samouraï (1967) | CRIME | 1967 | 2026-09-01 |\n"
            "| Unwatched | Stalker (1979) | SCIENCE FICTION | 1979 |  |\n",
            encoding="utf-8",
        )
        restored = self.root / "restored.md"

        with patch.object(add_command, "get_config", return_value={"list_path": str(restored)}):
            add_command.run_add(str(backup), None, yes=True)

        archive = MovieList(restored)
        self.assertEqual(2, archive.total_count())
        self.assertEqual("2026-09-01", archive.find_one("Le Samouraï").watched_on)
        self.assertFalse(archive.find_one("Stalker").watched)

    def test_classify_writes_only_a_companion_for_the_supplied_source(self):
        source = self.root / "list.md"
        source.write_text("Persona (1966)\n", encoding="utf-8")
        archive_before = self.archive.read_text(encoding="utf-8")
        config = {"list_path": str(self.archive), "groq_api_key": "test", "groq_model": "test-model"}
        fake_result = [{"title": "Persona (1966)", "genre_header": "AUTEUR CINEMA", "reason": "Test", "needs_review": False}]

        with patch.object(classify_command, "get_config", return_value=config), patch.object(
            classify_command, "classify_movies", return_value=fake_result
        ):
            classify_command.run_classify(str(source), write=True)

        self.assertEqual(archive_before, self.archive.read_text(encoding="utf-8"))
        self.assertTrue((self.root / "list.classified.md").exists())

    def test_classify_preserves_repeated_source_rows_for_later_archive_deduplication(self):
        source = self.root / "repeated.md"
        source.write_text(
            "AUTEUR\n\n- [ ] Aguirre, the Wrath of God (1972)\n\n"
            "EPIC\n\n- [x] Aguirre, the Wrath of God (1972)\n",
            encoding="utf-8",
        )
        config = {"list_path": str(self.archive), "groq_api_key": "test", "groq_model": "test-model"}
        fake_result = {
            "title": "Aguirre, the Wrath of God (1972)",
            "genre_header": "AUTEUR CINEMA",
            "reason": "Test",
            "needs_review": False,
        }

        with patch.object(classify_command, "get_config", return_value=config), patch.object(
            classify_command, "classify_movies", return_value=[fake_result, fake_result]
        ) as classify_movies:
            classify_command.run_classify(str(source), write=True)

        classify_movies.assert_called_once_with(
            "test",
            "test-model",
            ["Aguirre, the Wrath of God (1972)", "Aguirre, the Wrath of God (1972)"],
            tmdb_api_key=None,
            omdb_api_key=None,
            refresh_metadata=False,
        )
        output = (self.root / "repeated.classified.md").read_text(encoding="utf-8")
        self.assertEqual(2, output.count("Aguirre, the Wrath of God (1972)"))

    def test_classify_does_not_write_when_titles_remain_unresolved(self):
        source = self.root / "list.md"
        source.write_text("Persona (1966)\nUnclassifiable Film (1999)\n", encoding="utf-8")
        archive_before = self.archive.read_text(encoding="utf-8")
        config = {"list_path": str(self.archive), "groq_api_key": "test", "groq_model": "test-model"}
        fake_result = [
            {"title": "Persona (1966)", "genre_header": "AUTEUR CINEMA", "reason": "Test", "needs_review": False},
            {
                "title": "Unclassifiable Film (1999)",
                "genre_header": "UNCATEGORIZED IMPORTS",
                "reason": "AI did not return a valid classification for this title after retries.",
                "needs_review": True,
            },
        ]

        with patch.object(classify_command, "get_config", return_value=config), patch.object(
            classify_command, "classify_movies", return_value=fake_result
        ):
            classify_command.run_classify(str(source), write=True)

        # Nothing should be written anywhere, and the main archive is untouched.
        self.assertFalse((self.root / "list.classified.md").exists())
        self.assertEqual(archive_before, self.archive.read_text(encoding="utf-8"))

    def test_classify_writes_a_result_returned_after_metadata_fallback(self):
        source = self.root / "list.md"
        source.write_text("Persona (1966)\nStalker (1979)\n", encoding="utf-8")
        config = {"list_path": str(self.archive), "groq_api_key": "test", "groq_model": "test-model"}
        recovered = [
            {
                "title": "Persona (1966)",
                "genre_header": "AUTEUR CINEMA",
                "reason": "Verified through metadata fallback.",
                "needs_review": False,
                "confidence": 5,
                "runner_up_genre_number": None,
            },
            {
                "title": "Stalker (1979)",
                "genre_header": "SCIENCE FICTION",
                "reason": "Verified through metadata fallback.",
                "needs_review": False,
                "confidence": 4,
                "runner_up_genre_number": 1,
            },
        ]

        with patch.object(classify_command, "get_config", return_value=config), patch.object(
            classify_command, "classify_movies", return_value=recovered
        ):
            classify_command.run_classify(str(source), write=True)

        self.assertTrue((self.root / "list.classified.md").exists())

    def test_classify_shows_confidence_runner_up_and_forwards_refresh(self):
        source = self.root / "list.md"
        source.write_text("Persona (1966)\n", encoding="utf-8")
        config = {"list_path": str(self.archive), "groq_api_key": "test", "groq_model": "test-model"}
        result = [
            {
                "title": "Persona (1966)",
                "genre_header": "AUTEUR CINEMA",
                "reason": "Test",
                "needs_review": False,
                "confidence": 2,
                "runner_up_genre_number": 2,
            }
        ]

        with patch.object(classify_command, "get_config", return_value=config), patch.object(
            classify_command, "classify_movies", return_value=result
        ) as classify_movies, classify_command.console.capture() as capture:
            classify_command.run_classify(str(source), refresh_metadata=True)

        output = capture.get()
        self.assertIn("CONF.", output)
        self.assertIn("RUNNER-UP", output)
        self.assertIn("2/5", output)
        self.assertIn("low model confidence", output)
        classify_movies.assert_called_once_with(
            "test",
            "test-model",
            ["Persona (1966)"],
            tmdb_api_key=None,
            omdb_api_key=None,
            refresh_metadata=True,
        )

    def test_classify_force_writes_the_companion_despite_unresolved_titles(self):
        source = self.root / "list.md"
        source.write_text("Unclassifiable Film (1999)\n", encoding="utf-8")
        config = {"list_path": str(self.archive), "groq_api_key": "test", "groq_model": "test-model"}
        fake_result = [
            {
                "title": "Unclassifiable Film (1999)",
                "genre_header": "UNCATEGORIZED IMPORTS",
                "reason": "AI did not return a valid classification for this title after retries.",
                "needs_review": True,
            },
        ]

        with patch.object(classify_command, "get_config", return_value=config), patch.object(
            classify_command, "classify_movies", return_value=fake_result
        ):
            classify_command.run_classify(str(source), write=True, force=True)

        output = self.root / "list.classified.md"
        self.assertTrue(output.exists())
        self.assertIn("UNCATEGORIZED IMPORTS", output.read_text(encoding="utf-8"))

    def test_classify_refuses_the_configured_archive_as_its_source(self):
        config = {"list_path": str(self.archive), "groq_api_key": "test", "groq_model": "test-model"}
        with patch.object(classify_command, "get_config", return_value=config), patch.object(
            classify_command, "classify_movies"
        ) as classify_movies:
            classify_command.run_classify(str(self.archive), write=True)

        classify_movies.assert_not_called()
        self.assertFalse((self.root / "movies.classified.md").exists())


if __name__ == "__main__":
    unittest.main()

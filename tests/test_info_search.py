import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

try:
    import rich  # noqa: F401
    import typer  # noqa: F401
    from grimwatch.commands import info_cmd, search
    from grimwatch.metadata import VerifiedMetadata
    from grimwatch.parser import MovieList

    HAS_CLI_DEPS = True
except ModuleNotFoundError:
    HAS_CLI_DEPS = False


@unittest.skipUnless(HAS_CLI_DEPS, "requires the package's CLI dependencies")
class InfoAndSearchTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.archive = Path(self.temp_dir.name) / "movies.md"
        self.archive.write_text("FANTASY\n\n- [ ] Excalibur (1981)\n", encoding="utf-8")
        self.config = {"list_path": str(self.archive)}

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_inline_info_keeps_archive_record_and_adds_imdb_search_when_unverified(self):
        archive = MovieList(self.archive)
        unresolved = VerifiedMetadata.review("Excalibur (1981)", "No unique match.")

        with patch.object(info_cmd, "verify_film_metadata", return_value=[unresolved]), info_cmd.console.capture() as capture:
            info_cmd.show_inline_info("Excalibur (1981)", archive)

        output = capture.get()
        self.assertIn("Metadata lookup found no verified match for this title. Archive data only.", output)
        self.assertIn("Excalibur (1981)", output)
        self.assertIn("imdb.com/find/?q=Excalibur", output)

    def test_director_drill_down_reports_missing_director_metadata(self):
        unresolved = VerifiedMetadata(title="Excalibur (1981)", verified=True, reason="Test metadata.")

        with patch.object(search, "get_config", return_value=self.config), patch.object(
            search.Prompt, "ask", side_effect=["1", "d"]
        ), patch.object(search.Confirm, "ask", return_value=False), patch.object(
            search, "verify_film_metadata", return_value=[unresolved]
        ), search.console.capture() as capture:
            search.run_search("Excalibur", False, False)

        self.assertIn("No director information found for Excalibur (1981).", capture.get())

    def test_actor_drill_down_reports_no_related_archive_films(self):
        metadata = VerifiedMetadata(
            title="Excalibur (1981)",
            verified=True,
            reason="Test metadata.",
            cast=("Nicol Williamson",),
        )

        with patch.object(search, "get_config", return_value=self.config), patch.object(
            search.Prompt, "ask", side_effect=["1", "a"]
        ), patch.object(search.Confirm, "ask", return_value=False), patch.object(
            search, "verify_film_metadata", return_value=[metadata]
        ), patch.object(search, "_find_by_field", return_value=[]), search.console.capture() as capture:
            search.run_search("Excalibur", False, False)

        self.assertIn("No other films with Nicol Williamson", capture.get())


if __name__ == "__main__":
    unittest.main()

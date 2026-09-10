import tempfile
import unittest
from datetime import date
from pathlib import Path

from grimwatch.parser import MovieList, read_import_source


class MovieListTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.path = Path(self.temp_dir.name) / "movies.md"
        self.path.write_text(
            "# My archive\n\n"
            "## CRIME CINEMA — French Polar\n\n"
            "- [x] Le Samouraï (1967)\n"
            "* [ ] Le Cercle Rouge (1970)\n\n"
            "SCIENCE FICTION — Cerebral\n\n"
            "- [ ] Stalker (1979)\n",
            encoding="utf-8",
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_reads_headings_and_both_checkbox_markers(self):
        archive = MovieList(str(self.path))

        self.assertEqual(3, archive.total_count())
        self.assertEqual(1, archive.watched_count())
        self.assertEqual("Le Samouraï", archive.watched()[0].clean_title)

    def test_search_includes_genre_text_and_year(self):
        archive = MovieList(str(self.path))

        self.assertEqual(2, len(archive.find("crime")))
        self.assertEqual("Stalker (1979)", archive.find_one("1979").title)

    def test_fuzzy_shelf_matching_prefers_the_short_name(self):
        archive = MovieList(str(self.path))

        shelf = archive.find_genre_fuzzy("French Polar")

        self.assertIsNotNone(shelf)
        self.assertTrue(shelf.header.startswith("CRIME CINEMA"))

    def test_save_writes_a_clean_markdown_archive(self):
        archive = MovieList(str(self.path))
        archive.mark_watched(archive.find_one("Stalker"))
        archive.save()

        saved = self.path.read_text(encoding="utf-8")
        self.assertIn("- [x] Stalker (1979)", saved)
        self.assertFalse((self.path.parent / f".{self.path.name}.grimwatch.tmp").exists())

    def test_watched_timestamp_round_trips_and_clears_when_unwatched(self):
        archive = MovieList(str(self.path))
        stalker = archive.find_one("Stalker")
        archive.mark_watched(stalker)
        archive.save()

        expected = date.today().isoformat()
        saved = self.path.read_text(encoding="utf-8")
        self.assertIn(f"- [x] Stalker (1979) <!-- watched_on: {expected} -->", saved)

        reloaded = MovieList(str(self.path))
        stalker = reloaded.find_one("Stalker")
        self.assertEqual(expected, stalker.watched_on)
        reloaded.mark_watched(stalker, False)
        reloaded.save()

        self.assertNotIn("watched_on", self.path.read_text(encoding="utf-8"))

    def test_personal_note_round_trips_after_the_watched_annotation(self):
        archive = MovieList(str(self.path))
        stalker = archive.find_one("Stalker")
        archive.mark_watched(stalker)
        archive.set_note(stalker, "Beautiful, but leave time to think afterward.")
        archive.save()

        saved = self.path.read_text(encoding="utf-8")
        self.assertIn(
            "<!-- watched_on: " + date.today().isoformat() + " --> "
            "<!-- note: Beautiful, but leave time to think afterward. -->",
            saved,
        )
        reloaded = MovieList(str(self.path))
        self.assertEqual("Beautiful, but leave time to think afterward.", reloaded.find_one("Stalker").note)

    def test_personal_notes_are_capped_at_200_characters(self):
        archive = MovieList(str(self.path))
        stalker = archive.find_one("Stalker")
        archive.set_note(stalker, "x" * 240)

        self.assertEqual("x" * 200, stalker.note)

    def test_reads_export_table_as_a_categorized_archive_backup(self):
        exported = self.path.parent / "export.md"
        exported.write_text(
            "# Grimwatch Export\n\n"
            "| Status | Title | Shelf | Year | Watched On |\n"
            "| --- | --- | --- | --- | --- |\n"
            "| Watched | Le Samouraï (1967) | CRIME CINEMA — French Polar | 1967 | 2026-09-01 |\n"
            "| Unwatched | Stalker | SCIENCE FICTION — Cerebral | 1979 |  |\n",
            encoding="utf-8",
        )

        source = read_import_source(exported)

        self.assertEqual("grimwatch-export", source.format)
        self.assertEqual(2, len(source.entries))
        self.assertEqual("Le Samouraï (1967)", source.entries[0].title)
        self.assertEqual("2026-09-01", source.entries[0].watched_on)
        self.assertEqual("Stalker (1979)", source.entries[1].title)
        self.assertFalse(source.entries[1].watched)


if __name__ == "__main__":
    unittest.main()

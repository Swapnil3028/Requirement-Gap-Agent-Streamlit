"""Tests for persistent SQLite review history."""

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from history_store import load_review_history, save_review


class ReviewHistoryTests(unittest.TestCase):
    def test_saves_each_run_and_returns_newest_first(self) -> None:
        dashboard = {
            "score": 82,
            "ambiguities": 5,
            "missing_ac": 3,
            "conflicts": 1,
            "risk": "Medium",
        }
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "history.sqlite3"

            save_review("BRD-A.pdf", dashboard, database_path)
            save_review("BRD-B.docx", dashboard, database_path)

            history = load_review_history(database_path)

        self.assertEqual(len(history), 2)
        self.assertEqual(
            {entry["document_name"] for entry in history},
            {"BRD-A.pdf", "BRD-B.docx"},
        )
        for entry in history:
            self.assertEqual(entry["score"], 82)
            self.assertEqual(entry["ambiguities"], 5)
            self.assertEqual(entry["missing_ac"], 3)
            self.assertEqual(entry["conflict"], 1)
            self.assertEqual(entry["risk"], "Medium")
            self.assertEqual(entry["score_model"], "severity_weighted")
            self.assertTrue(entry["date"])

    def test_empty_database_returns_empty_history(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            history = load_review_history(Path(directory) / "history.sqlite3")

        self.assertEqual(history, [])

    def test_adds_risk_column_to_existing_history_database(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "history.sqlite3"
            with closing(sqlite3.connect(database_path)) as connection:
                with connection:
                    connection.execute(
                        """
                        CREATE TABLE review_history (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            document_name TEXT NOT NULL,
                            date TEXT NOT NULL,
                            score INTEGER NOT NULL,
                            ambiguities INTEGER NOT NULL,
                            missing_ac INTEGER NOT NULL,
                            conflict INTEGER NOT NULL
                        )
                        """
                    )
                    connection.execute(
                        """
                        INSERT INTO review_history
                            (document_name, date, score, ambiguities, missing_ac, conflict)
                        VALUES ('Old.pdf', '2026-10-09T10:00:00+00:00', 80, 1, 2, 0)
                        """
                    )

            history = load_review_history(database_path)

        self.assertEqual(history[0]["document_name"], "Old.pdf")
        self.assertEqual(history[0]["risk"], "Unknown")
        self.assertEqual(history[0]["score_model"], "severity_weighted")

    def test_quality_scoring_model_is_saved_for_new_review(self) -> None:
        dashboard = {
            "score": 85,
            "ambiguities": 1,
            "missing_ac": 0,
            "conflicts": 0,
            "risk": "Low",
            "score_model": "requirement_quality_v1",
        }
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "history.sqlite3"

            save_review("BRD.pdf", dashboard, database_path)
            history = load_review_history(database_path)

        self.assertEqual(history[0]["score_model"], "requirement_quality_v1")


if __name__ == "__main__":
    unittest.main()

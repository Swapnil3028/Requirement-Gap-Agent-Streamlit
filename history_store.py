"""Persistent SQLite storage for requirement-review dashboard history."""

import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import Mapping, TypedDict


class ReviewHistoryEntry(TypedDict):
    document_name: str
    date: str
    score: int
    score_model: str
    ambiguities: int
    missing_ac: int
    conflict: int
    risk: str


class ReviewHistoryError(Exception):
    """Raised when review history cannot be stored or retrieved."""


DEFAULT_HISTORY_PATH = Path(__file__).resolve().parent / ".history" / "reviews.sqlite3"


def _connect(database_path: str | Path | None) -> sqlite3.Connection:
    path = Path(database_path) if database_path is not None else DEFAULT_HISTORY_PATH
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(path)
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS review_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                document_name TEXT NOT NULL,
                date TEXT NOT NULL,
                score INTEGER NOT NULL CHECK (score BETWEEN 0 AND 100),
                ambiguities INTEGER NOT NULL CHECK (ambiguities >= 0),
                missing_ac INTEGER NOT NULL CHECK (missing_ac >= 0),
                conflict INTEGER NOT NULL CHECK (conflict >= 0),
                risk TEXT NOT NULL DEFAULT 'Unknown',
                score_model TEXT NOT NULL DEFAULT 'severity_weighted'
            )
            """
        )
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(review_history)")
        }
        if "risk" not in columns:
            connection.execute(
                "ALTER TABLE review_history "
                "ADD COLUMN risk TEXT NOT NULL DEFAULT 'Unknown'"
            )
            connection.commit()
        if "score_model" not in columns:
            connection.execute(
                "ALTER TABLE review_history "
                "ADD COLUMN score_model TEXT NOT NULL DEFAULT 'severity_weighted'"
            )
            connection.commit()
        return connection
    except (OSError, sqlite3.Error) as exc:
        raise ReviewHistoryError(f"Could not open review history database: {exc}") from exc


def save_review(
    document_name: str,
    dashboard: Mapping[str, int | str],
    database_path: str | Path | None = None,
) -> None:
    """Append a completed analysis run to the local SQLite history."""
    with closing(_connect(database_path)) as connection:
        try:
            with connection:
                connection.execute(
                    """
                    INSERT INTO review_history
                        (document_name, date, score, ambiguities, missing_ac, conflict, risk, score_model)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        document_name,
                        datetime.now().astimezone().isoformat(timespec="seconds"),
                        dashboard["score"],
                        dashboard["ambiguities"],
                        dashboard["missing_ac"],
                        dashboard["conflicts"],
                        dashboard["risk"],
                        dashboard.get("score_model", "severity_weighted"),
                    ),
                )
        except sqlite3.Error as exc:
            raise ReviewHistoryError(f"Could not save review history: {exc}") from exc


def load_review_history(
    database_path: str | Path | None = None,
) -> list[ReviewHistoryEntry]:
    """Return all saved runs, newest first."""
    with closing(_connect(database_path)) as connection:
        try:
            rows = connection.execute(
                """
                SELECT document_name, date, score, ambiguities, missing_ac, conflict
                    , risk, score_model
                FROM review_history
                ORDER BY date DESC, id DESC
                """
            ).fetchall()
        except sqlite3.Error as exc:
            raise ReviewHistoryError(f"Could not load review history: {exc}") from exc
    return [
        {
            "document_name": str(row[0]),
            "date": str(row[1]),
            "score": int(row[2]),
            "ambiguities": int(row[3]),
            "missing_ac": int(row[4]),
            "conflict": int(row[5]),
            "risk": str(row[6]),
            "score_model": str(row[7]),
        }
        for row in rows
    ]

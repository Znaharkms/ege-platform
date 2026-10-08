from __future__ import annotations

import sqlite3
from pathlib import Path

from app.importing.sqlite_content import (
    _date_precision,
    discover_source_files,
    load_history_dates,
)


def test_discover_source_files_accepts_download_suffixes(tmp_path: Path) -> None:
    expected = {
        "history_dates": "history_dates(1).db",
        "history_terms": "history_terms(1).db",
        "society_terms": "society_obsh(1).db",
        "society_plans": "society_plans(1).db",
    }
    for filename in expected.values():
        (tmp_path / filename).touch()

    found = discover_source_files(tmp_path)

    assert {key: path.name for key, path in found.items()} == expected


def test_history_dates_loader_reads_without_modifying_source(tmp_path: Path) -> None:
    path = tmp_path / "history_dates.db"
    connection = sqlite3.connect(path)
    connection.execute(
        """
        CREATE TABLE dates (
            id INTEGER PRIMARY KEY,
            period TEXT NOT NULL,
            date_text TEXT NOT NULL,
            date_start_iso TEXT,
            date_end_iso TEXT,
            date_display TEXT,
            date_dot TEXT,
            event TEXT NOT NULL,
            source TEXT
        )
        """
    )
    connection.execute(
        """
        INSERT INTO dates VALUES (
            1, 'XIX век', '1812 г.', '1812-01-01', '1812-12-31',
            '1812', '1812', 'Отечественная война', NULL
        )
        """
    )
    connection.commit()
    connection.close()
    before = path.read_bytes()

    loaded = load_history_dates(path)

    assert not loaded.errors
    assert len(loaded.records) == 1
    assert loaded.records[0].typed["precision"] == "year"
    assert loaded.records[0].typed["event_text"] == "Отечественная война"
    assert path.read_bytes() == before


def test_date_precision_recognizes_range() -> None:
    from datetime import date

    assert _date_precision("1941–1945 гг.", date(1941, 1, 1), date(1945, 12, 31)) == "range"

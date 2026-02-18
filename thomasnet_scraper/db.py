"""SQLite database: schema, UPSERT, session/page tracking, export."""

from __future__ import annotations

import csv
import io
import logging
import sqlite3
from datetime import datetime, timezone
from typing import Optional

from thomasnet_scraper.config import DEFAULT_DB_PATH
from thomasnet_scraper.models import Supplier

log = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS suppliers (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id      TEXT    NOT NULL UNIQUE,
    company_name    TEXT    NOT NULL,
    search_term     TEXT,
    location        TEXT,
    state           TEXT,
    phone           TEXT,
    website         TEXT,
    profile_url     TEXT,
    description     TEXT,
    company_type    TEXT,
    annual_revenue  TEXT,
    num_employees   TEXT,
    year_founded    TEXT,
    brands          TEXT,
    scraped_at      TEXT    NOT NULL,
    profile_scraped INTEGER NOT NULL DEFAULT 0,
    updated_at      TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS scrape_sessions (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    search_term      TEXT    NOT NULL,
    heading          TEXT,
    total_pages      INTEGER,
    last_page        INTEGER NOT NULL DEFAULT 0,
    total_suppliers  INTEGER,
    status           TEXT    NOT NULL DEFAULT 'running',
    started_at       TEXT    NOT NULL,
    updated_at       TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS scrape_pages (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      INTEGER NOT NULL REFERENCES scrape_sessions(id),
    page_number     INTEGER NOT NULL,
    status          TEXT    NOT NULL DEFAULT 'pending',
    suppliers_found INTEGER NOT NULL DEFAULT 0,
    error_message   TEXT,
    scraped_at      TEXT,
    UNIQUE(session_id, page_number)
);

CREATE TABLE IF NOT EXISTS supplier_profiles (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id      TEXT    NOT NULL UNIQUE REFERENCES suppliers(company_id),
    raw_html        TEXT,
    scraped_at      TEXT
);
"""


class Database:
    def __init__(self, db_path: str = DEFAULT_DB_PATH) -> None:
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self._init_schema()

    def _init_schema(self) -> None:
        self.conn.executescript(_SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    # ── Suppliers ─────────────────────────────────────────────────────

    def upsert_supplier(self, s: Supplier) -> None:
        now = _now()
        self.conn.execute(
            """
            INSERT INTO suppliers (
                company_id, company_name, search_term, location, state,
                phone, website, profile_url, description, company_type,
                annual_revenue, num_employees, year_founded, brands,
                scraped_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(company_id) DO UPDATE SET
                company_name   = excluded.company_name,
                location       = excluded.location,
                state          = excluded.state,
                phone          = excluded.phone,
                website        = excluded.website,
                profile_url    = excluded.profile_url,
                description    = excluded.description,
                company_type   = excluded.company_type,
                annual_revenue = excluded.annual_revenue,
                num_employees  = excluded.num_employees,
                year_founded   = excluded.year_founded,
                brands         = excluded.brands,
                updated_at     = excluded.updated_at
            """,
            (
                s.company_id, s.company_name, s.search_term, s.location,
                s.state, s.phone, s.website, s.profile_url, s.description,
                s.company_type, s.annual_revenue, s.num_employees,
                s.year_founded, s.brands, now, now,
            ),
        )

    def upsert_suppliers(self, suppliers: list[Supplier]) -> None:
        for s in suppliers:
            self.upsert_supplier(s)
        self.conn.commit()

    def get_suppliers_by_search(self, search_term: str) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM suppliers WHERE search_term = ? ORDER BY company_name",
            (search_term,),
        ).fetchall()

    def count_suppliers(self, search_term: Optional[str] = None) -> int:
        if search_term:
            row = self.conn.execute(
                "SELECT COUNT(*) FROM suppliers WHERE search_term = ?",
                (search_term,),
            ).fetchone()
        else:
            row = self.conn.execute("SELECT COUNT(*) FROM suppliers").fetchone()
        return row[0]

    # ── Sessions ──────────────────────────────────────────────────────

    def create_session(
        self,
        search_term: str,
        heading: Optional[str] = None,
        total_pages: Optional[int] = None,
        total_suppliers: Optional[int] = None,
    ) -> int:
        now = _now()
        cur = self.conn.execute(
            """
            INSERT INTO scrape_sessions
                (search_term, heading, total_pages, total_suppliers, started_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (search_term, heading, total_pages, total_suppliers, now, now),
        )
        self.conn.commit()
        return cur.lastrowid

    def update_session(
        self,
        session_id: int,
        *,
        total_pages: Optional[int] = None,
        total_suppliers: Optional[int] = None,
        last_page: Optional[int] = None,
        status: Optional[str] = None,
    ) -> None:
        parts: list[str] = ["updated_at = ?"]
        params: list[object] = [_now()]
        if total_pages is not None:
            parts.append("total_pages = ?")
            params.append(total_pages)
        if total_suppliers is not None:
            parts.append("total_suppliers = ?")
            params.append(total_suppliers)
        if last_page is not None:
            parts.append("last_page = ?")
            params.append(last_page)
        if status is not None:
            parts.append("status = ?")
            params.append(status)
        params.append(session_id)
        self.conn.execute(
            f"UPDATE scrape_sessions SET {', '.join(parts)} WHERE id = ?",
            params,
        )
        self.conn.commit()

    def get_resumable_session(
        self, search_term: str, heading: Optional[str] = None
    ) -> Optional[sqlite3.Row]:
        """Return the most recent non-completed session for this search."""
        return self.conn.execute(
            """
            SELECT * FROM scrape_sessions
            WHERE search_term = ? AND (heading = ? OR (heading IS NULL AND ? IS NULL))
              AND status != 'completed'
            ORDER BY id DESC LIMIT 1
            """,
            (search_term, heading, heading),
        ).fetchone()

    def get_all_sessions(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM scrape_sessions ORDER BY id DESC"
        ).fetchall()

    # ── Pages ─────────────────────────────────────────────────────────

    def ensure_pages(self, session_id: int, total_pages: int) -> None:
        """Create pending page rows for any pages not yet tracked."""
        for pg in range(1, total_pages + 1):
            self.conn.execute(
                """
                INSERT OR IGNORE INTO scrape_pages (session_id, page_number)
                VALUES (?, ?)
                """,
                (session_id, pg),
            )
        self.conn.commit()

    def get_pending_pages(self, session_id: int) -> list[int]:
        rows = self.conn.execute(
            """
            SELECT page_number FROM scrape_pages
            WHERE session_id = ? AND status IN ('pending', 'failed')
            ORDER BY page_number
            """,
            (session_id,),
        ).fetchall()
        return [r["page_number"] for r in rows]

    def mark_page_done(
        self, session_id: int, page_number: int, suppliers_found: int
    ) -> None:
        self.conn.execute(
            """
            UPDATE scrape_pages
            SET status = 'success', suppliers_found = ?, scraped_at = ?
            WHERE session_id = ? AND page_number = ?
            """,
            (suppliers_found, _now(), session_id, page_number),
        )
        self.conn.commit()

    def mark_page_failed(
        self, session_id: int, page_number: int, error: str
    ) -> None:
        self.conn.execute(
            """
            UPDATE scrape_pages
            SET status = 'failed', error_message = ?, scraped_at = ?
            WHERE session_id = ? AND page_number = ?
            """,
            (error, _now(), session_id, page_number),
        )
        self.conn.commit()

    def get_page_stats(self, session_id: int) -> dict[str, int]:
        rows = self.conn.execute(
            """
            SELECT status, COUNT(*) as cnt FROM scrape_pages
            WHERE session_id = ? GROUP BY status
            """,
            (session_id,),
        ).fetchall()
        return {r["status"]: r["cnt"] for r in rows}

    # ── Export ─────────────────────────────────────────────────────────

    def export_csv(self, search_term: str, path: str) -> int:
        rows = self.get_suppliers_by_search(search_term)
        if not rows:
            return 0
        columns = rows[0].keys()
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(columns)
            for row in rows:
                writer.writerow(tuple(row))
        return len(rows)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()

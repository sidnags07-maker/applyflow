"""SQLite-backed funnel tracker.

Why SQLite and not a CSV? Because the whole point of a funnel is *state that
survives re-syncs*: when you pull the repo again next week and 50 new roles
appeared, you must not lose which ones you already applied to. SQLite gives us a
cheap UPSERT keyed on the job's stable hash. (Your intern-pipeline-prep step 05
covers this same idea.)
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from typing import Iterable, List, Optional

from .models import Job, STATUSES, _now

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    key        TEXT PRIMARY KEY,
    company    TEXT NOT NULL,
    title      TEXT NOT NULL,
    url        TEXT NOT NULL,
    location   TEXT DEFAULT '',
    posted     TEXT DEFAULT '',
    source     TEXT DEFAULT '',
    status     TEXT DEFAULT 'new',
    applied_at TEXT DEFAULT '',
    notes      TEXT DEFAULT '',
    draft_path TEXT DEFAULT '',
    first_seen TEXT DEFAULT ''
);
"""


class Store:
    def __init__(self, path: str):
        self.path = path
        with self._conn() as c:
            c.executescript(_SCHEMA)

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def upsert_many(self, jobs: Iterable[Job]) -> tuple[int, int]:
        """Insert new jobs, refresh mutable fields on existing ones.

        Crucially we DO NOT touch status/applied_at/notes/draft_path on rows that
        already exist — those are your decisions, not the repo's. Returns
        (added, seen_again).
        """
        added = seen = 0
        with self._conn() as c:
            for j in jobs:
                row = c.execute("SELECT key FROM jobs WHERE key=?", (j.key,)).fetchone()
                if row:
                    c.execute(
                        "UPDATE jobs SET url=?, location=?, posted=?, source=? WHERE key=?",
                        (j.url, j.location, j.posted, j.source, j.key),
                    )
                    seen += 1
                else:
                    c.execute(
                        """INSERT INTO jobs
                           (key, company, title, url, location, posted, source,
                            status, applied_at, notes, draft_path, first_seen)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (j.key, j.company, j.title, j.url, j.location, j.posted,
                         j.source, "new", "", "", "", j.first_seen),
                    )
                    added += 1
        return added, seen

    def list(self, status: Optional[str] = None, limit: int = 0,
             search: str = "") -> List[Job]:
        q = "SELECT * FROM jobs"
        clauses, params = [], []
        if status:
            clauses.append("status=?")
            params.append(status)
        if search:
            clauses.append("(company LIKE ? OR title LIKE ?)")
            params += [f"%{search}%", f"%{search}%"]
        if clauses:
            q += " WHERE " + " AND ".join(clauses)
        q += " ORDER BY first_seen DESC"
        if limit:
            q += f" LIMIT {int(limit)}"
        with self._conn() as c:
            return [self._to_job(r) for r in c.execute(q, params).fetchall()]

    def get(self, key: str) -> Optional[Job]:
        with self._conn() as c:
            # allow prefix match so you can type the first few chars of a key
            r = c.execute(
                "SELECT * FROM jobs WHERE key=? OR key LIKE ? LIMIT 1",
                (key, key + "%"),
            ).fetchone()
            return self._to_job(r) if r else None

    def set_status(self, key: str, status: str, note: str = "") -> bool:
        if status not in STATUSES:
            raise ValueError(f"unknown status {status!r}; choose from {STATUSES}")
        applied_at = _now() if status == "applied" else ""
        with self._conn() as c:
            cur = c.execute(
                "UPDATE jobs SET status=?, applied_at=CASE WHEN ?='' THEN applied_at ELSE ? END, "
                "notes=CASE WHEN ?='' THEN notes ELSE ? END "
                "WHERE key=? OR key LIKE ?",
                (status, applied_at, applied_at, note, note, key, key + "%"),
            )
            return cur.rowcount > 0

    def set_draft(self, key: str, path: str) -> None:
        with self._conn() as c:
            c.execute("UPDATE jobs SET draft_path=?, status=CASE WHEN status='new' "
                      "THEN 'drafted' ELSE status END WHERE key=? OR key LIKE ?",
                      (path, key, key + "%"))

    def counts(self) -> dict:
        with self._conn() as c:
            rows = c.execute("SELECT status, COUNT(*) n FROM jobs GROUP BY status").fetchall()
        return {r["status"]: r["n"] for r in rows}

    @staticmethod
    def _to_job(r: sqlite3.Row) -> Job:
        return Job(
            company=r["company"], title=r["title"], url=r["url"],
            location=r["location"], posted=r["posted"], source=r["source"],
            status=r["status"], applied_at=r["applied_at"], notes=r["notes"],
            draft_path=r["draft_path"], first_seen=r["first_seen"],
        )

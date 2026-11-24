import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone

from .config import DB_PATH

_lock = threading.RLock()

ACTIVE = ("queued", "tailoring", "applying")
STATUSES = ("new", "seen", "queued", "tailoring", "applying", "needs_manual",
            "blocked", "failed", "submitted", "skipped")

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    company TEXT NOT NULL,
    title TEXT NOT NULL,
    location TEXT,
    url TEXT NOT NULL,
    apply_url TEXT,
    posted_at TEXT,
    first_seen TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    description TEXT,
    exp_min REAL,
    fit TEXT,
    ats TEXT,
    status TEXT NOT NULL DEFAULT 'new',
    status_detail TEXT,
    resume_path TEXT,
    confirmation TEXT,
    active INTEGER NOT NULL DEFAULT 1
);
CREATE UNIQUE INDEX IF NOT EXISTS jobs_url ON jobs(url);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT,
    ts TEXT NOT NULL,
    level TEXT NOT NULL,
    message TEXT NOT NULL
);
"""


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def connect():
    with _lock:
        conn = sqlite3.connect(DB_PATH, timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()


def init():
    with connect() as conn:
        conn.executescript(SCHEMA)
        # Work interrupted by a server restart must not stay "in progress" forever.
        conn.execute(
            "UPDATE jobs SET status='failed', status_detail='Interrupted by server restart; click Apply to retry' "
            "WHERE status IN ('tailoring','applying')"
        )


def upsert_found(job):
    """Insert a newly discovered job. Returns True when the job is new."""
    with connect() as conn:
        existing = conn.execute("SELECT id FROM jobs WHERE id=? OR url=?", (job["id"], job["url"])).fetchone()
        stamp = now()
        if existing:
            conn.execute(
                "UPDATE jobs SET title=?, location=?, apply_url=COALESCE(?, apply_url), posted_at=COALESCE(?, posted_at), "
                "description=COALESCE(?, description), exp_min=?, fit=?, ats=?, active=1 WHERE id=?",
                (job["title"], job["location"], job.get("apply_url"), job.get("posted_at"), job.get("description"),
                 job.get("exp_min"), job.get("fit"), job.get("ats"), existing["id"]),
            )
            return False
        conn.execute(
            "INSERT INTO jobs (id, source, company, title, location, url, apply_url, posted_at, first_seen, updated_at, "
            "description, exp_min, fit, ats, status) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (job["id"], job["source"], job["company"], job["title"], job["location"], job["url"], job.get("apply_url"),
             job.get("posted_at"), stamp, stamp, job.get("description"), job.get("exp_min"), job.get("fit"),
             job.get("ats"), job.get("status", "new")),
        )
        return True


def known_ids():
    with connect() as conn:
        return {row["id"] for row in conn.execute("SELECT id FROM jobs")}


def get(job_id):
    with connect() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        return dict(row) if row else None


def list_jobs():
    with connect() as conn:
        rows = conn.execute(
            "SELECT id, source, company, title, location, url, apply_url, posted_at, first_seen, updated_at, exp_min, "
            "fit, ats, status, status_detail, resume_path, confirmation FROM jobs WHERE active=1"
        ).fetchall()
        return [dict(row) for row in rows]


def set_status(job_id, status, detail=None, **fields):
    assert status in STATUSES, status
    columns = {"status": status, "status_detail": detail, "updated_at": now(), **fields}
    assignments = ", ".join(f"{key}=?" for key in columns)
    with connect() as conn:
        conn.execute(f"UPDATE jobs SET {assignments} WHERE id=?", (*columns.values(), job_id))
    log(job_id, "info" if status not in ("failed", "blocked") else "error", f"{status}: {detail or ''}".strip())


def log(job_id, level, message):
    with connect() as conn:
        conn.execute("INSERT INTO events (job_id, ts, level, message) VALUES (?,?,?,?)", (job_id, now(), level, message))


def events(job_id=None, limit=200):
    with connect() as conn:
        if job_id:
            rows = conn.execute("SELECT * FROM events WHERE job_id=? ORDER BY id DESC LIMIT ?", (job_id, limit))
        else:
            rows = conn.execute(
                "SELECT e.*, j.company, j.title FROM events e LEFT JOIN jobs j ON j.id=e.job_id ORDER BY e.id DESC LIMIT ?",
                (limit,),
            )
        return [dict(row) for row in rows]

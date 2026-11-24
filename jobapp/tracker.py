"""Bridge to the existing Excel tracker (Job Applications.xlsx) and its guarded record_job.py writer."""
import subprocess
import sys
import threading
from pathlib import Path

from . import db
from .config import load_settings
from .filters import detect_ats

_lock = threading.Lock()


def _paths():
    folder = Path(load_settings()["tracker_dir"])
    return folder / "Job Applications.xlsx", folder / "record_job.py"


def _map_status(status):
    status = (status or "").lower()
    if status == "submitted":
        return "submitted"
    if "captcha" in status or "block" in status:
        return "blocked"
    if "manual" in status or "verif" in status or "unconfirmed" in status:
        return "needs_manual"
    if "skip" in status or "not eligible" in status or "closed" in status:
        return "skipped"
    return "seen"


def import_rows():
    tracker, _ = _paths()
    if not tracker.exists():
        return 0
    from openpyxl import load_workbook
    workbook = load_workbook(tracker, read_only=True)
    sheet = workbook["Applications"]
    added = 0
    for row in sheet.iter_rows(min_row=2, values_only=True):
        company, role, location, experience, url, apply_url, status = row[:7]
        if not url or not company:
            continue
        resume, comments, confirmation = row[10], row[11], row[9]
        is_new = db.upsert_found({
            "id": f"tracker:{url}", "source": "tracker", "company": company, "title": role or "",
            "location": location or "", "url": url, "apply_url": apply_url or url, "description": None,
            "fit": "tracked", "ats": detect_ats(apply_url or url), "status": _map_status(status),
        })
        if is_new:
            added += 1
            mapped = _map_status(status)
            db.set_status(f"tracker:{url}", mapped, f"Imported from tracker: {status}. {comments or ''}".strip(),
                          resume_path=resume, confirmation=confirmation)
    workbook.close()
    return added


def record(job, status, confirmation=None, comments=None):
    """Writes through record_job.py, which refuses 'Submitted' without a confirmation."""
    tracker, script = _paths()
    if not script.exists():
        return "record_job.py not found; tracker not updated"
    args = [
        sys.executable, str(script), "--url", job["url"], "--company", job["company"], "--role", job["title"],
        "--location", job.get("location") or "India", "--status", status,
        "--experience", f"{job['exp_min']:g}+ years" if job.get("exp_min") is not None else "0-2 years / not stated",
        "--apply-url", job.get("apply_url") or job["url"],
    ]
    if job.get("resume_path") and Path(job["resume_path"]).is_file():
        args += ["--resume", job["resume_path"]]
    if confirmation:
        args += ["--confirmation", confirmation[:500]]
    if comments:
        args += ["--comments", comments[:500]]
    with _lock:
        result = subprocess.run(args, cwd=script.parent, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        return f"Tracker update failed: {(result.stderr or result.stdout).strip()[-300:]}"
    return None

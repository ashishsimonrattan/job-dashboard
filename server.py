"""Job dashboard server: python server.py  ->  http://127.0.0.1:8765"""
import json
import mimetypes
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from jobapp.config import STATIC, load_settings, setup_environment, update_settings

setup_environment()

from jobapp import db, sources, tracker  # noqa: E402  (needs setup_environment first)
from jobapp.applier import Applier  # noqa: E402


class Refresher:
    def __init__(self):
        self.lock = threading.Lock()
        self.running = False
        self.last_started = None
        self.last_finished = None
        self.last_new = []
        self.report = []
        self.error = None
        self.next_poll = None

    def trigger(self):
        if self.running:
            return False
        threading.Thread(target=self.run, daemon=True).start()
        return True

    def run(self):
        with self.lock:
            if self.running:
                return
            self.running = True
        self.last_started = db.now()
        try:
            jobs, report = sources.scan(load_settings(), db.known_ids())
            new_ids = [job["id"] for job in jobs if db.upsert_found(job)]
            for job_id in new_ids:
                db.log(job_id, "info", "New opening discovered")
            self.last_new = new_ids
            self.report = report
            self.error = None
        except Exception as exc:
            self.error = f"{type(exc).__name__}: {exc}"
            traceback.print_exc()
        finally:
            self.last_finished = db.now()
            self.running = False

    def poll_forever(self):
        while True:
            settings = load_settings()
            interval = max(30, int(settings.get("poll_seconds", 60)))
            self.next_poll = time.time() + interval
            if settings.get("poll_enabled", True):
                self.run()
            time.sleep(max(1, self.next_poll - time.time()))


refresher = Refresher()
applier = None


def stats(jobs):
    counts = {}
    for job in jobs:
        counts[job["status"]] = counts.get(job["status"], 0) + 1
    return counts


class Handler(BaseHTTPRequestHandler):
    server_version = "JobDashboard/1.0"

    def log_message(self, fmt, *args):
        if "/api/jobs" not in self.path and "/api/status" not in self.path:
            super().log_message(fmt, *args)

    def _json(self, payload, status=200):
        body = json.dumps(payload, default=str).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _file(self, path, download_name=None):
        if not path.is_file():
            return self._json({"error": "not found"}, 404)
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(path.name)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        if download_name:
            self.send_header("Content-Disposition", f'inline; filename="{download_name}"')
        self.end_headers()
        self.wfile.write(data)

    def _body(self):
        length = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(length) or b"{}") if length else {}

    def _same_origin(self):
        # Blocks other websites from driving this local API (CSRF); the UI always sends this header.
        return self.headers.get("X-Requested-With") == "job-dashboard"

    def do_GET(self):
        url = urlparse(self.path)
        path = url.path
        if path in ("/", "/index.html"):
            return self._file(STATIC / "index.html")
        if path.startswith("/static/"):
            target = (STATIC / unquote(path[len("/static/"):])).resolve()
            if STATIC.resolve() not in target.parents:
                return self._json({"error": "forbidden"}, 403)
            return self._file(target)
        if path == "/api/jobs":
            jobs = db.list_jobs()
            return self._json({"jobs": jobs, "stats": stats(jobs)})
        if path == "/api/status":
            settings = load_settings()
            return self._json({
                "refreshing": refresher.running, "last_started": refresher.last_started,
                "last_finished": refresher.last_finished, "last_new": refresher.last_new,
                "report": refresher.report, "error": refresher.error,
                "next_poll_in": max(0, int((refresher.next_poll or time.time()) - time.time())),
                "queue": applier.pending(), "current": applier.current,
                "settings": {k: settings[k] for k in ("poll_enabled", "poll_seconds", "auto_submit")},
            })
        if path.startswith("/api/jobs/"):
            job_id = unquote(path[len("/api/jobs/"):])
            job = db.get(job_id)
            if not job:
                return self._json({"error": "not found"}, 404)
            return self._json({"job": job, "events": db.events(job_id, 50)})
        if path == "/api/events":
            limit = int(parse_qs(url.query).get("limit", ["100"])[0])
            return self._json({"events": db.events(None, min(limit, 500))})
        if path.startswith("/api/resume/"):
            job = db.get(unquote(path[len("/api/resume/"):]))
            if not job or not job.get("resume_path"):
                return self._json({"error": "no resume yet"}, 404)
            resume = Path(job["resume_path"])
            return self._file(resume, resume.name)
        return self._json({"error": "not found"}, 404)

    def do_POST(self):
        if not self._same_origin():
            return self._json({"error": "missing X-Requested-With header"}, 403)
        path = urlparse(self.path).path
        try:
            body = self._body()
        except json.JSONDecodeError:
            return self._json({"error": "invalid JSON"}, 400)

        if path == "/api/refresh":
            return self._json({"started": refresher.trigger()})
        if path == "/api/apply":
            ids = [str(i) for i in body.get("ids", [])][:200]
            return self._json({"queued": applier.enqueue(ids)})
        if path == "/api/settings":
            return self._json({"settings": update_settings(body)})
        if path == "/api/import-tracker":
            return self._json({"imported": tracker.import_rows()})

        parts = path.strip("/").split("/")
        if len(parts) == 4 and parts[:2] == ["api", "jobs"]:
            job_id, action = unquote(parts[2]), parts[3]
            job = db.get(job_id)
            if not job:
                return self._json({"error": "not found"}, 404)
            if action == "skip":
                if job["status"] == "submitted":
                    return self._json({"error": "already submitted"}, 409)
                db.set_status(job_id, "skipped", body.get("reason") or "Skipped by user")
            elif action == "reset":
                if job["status"] == "submitted":
                    return self._json({"error": "already submitted"}, 409)
                db.set_status(job_id, "seen", "Reset by user")
            elif action == "seen":
                if job["status"] == "new":
                    db.set_status(job_id, "seen", "Reviewed")
            elif action == "open":
                applier.open_manual(job_id)
            elif action == "mark-submitted":
                confirmation = (body.get("confirmation") or "").strip()
                if len(confirmation) < 5:
                    return self._json({"error": "Paste the confirmation message or email subject"}, 400)
                db.set_status(job_id, "submitted", "Marked submitted by user", confirmation=confirmation)
                note = tracker.record(db.get(job_id), "Submitted", confirmation=confirmation,
                                      comments="Submitted manually; confirmed by user in dashboard")
                if note:
                    db.log(job_id, "error", note)
                    return self._json({"ok": True, "warning": note})
            else:
                return self._json({"error": "unknown action"}, 404)
            return self._json({"ok": True, "job": db.get(job_id)})
        return self._json({"error": "not found"}, 404)


def main():
    global applier
    db.init()
    try:
        imported = tracker.import_rows()
        print(f"Imported {imported} new rows from the Excel tracker")
    except Exception as exc:
        print(f"Tracker import skipped: {exc}")
    applier = Applier()
    threading.Thread(target=refresher.poll_forever, daemon=True).start()
    settings = load_settings()
    server = ThreadingHTTPServer((settings["host"], settings["port"]), Handler)
    print(f"Job dashboard running at http://{settings['host']}:{settings['port']}")
    server.serve_forever()


if __name__ == "__main__":
    main()

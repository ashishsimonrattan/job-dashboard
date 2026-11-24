import json
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
STATIC = ROOT / "static"
RESUME_DIR = ROOT / "resume"
BUILD_DIR = DATA / "resume-build"
BROWSER_PROFILE = DATA / "browser-profile"
DB_PATH = DATA / "jobs.db"
SETTINGS_PATH = ROOT / "settings.json"
PROFILE_PATH = ROOT / "profile.json"

_lock = threading.Lock()


def load_settings():
    with _lock:
        return json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))


def update_settings(changes):
    allowed = {"poll_enabled", "poll_seconds", "auto_submit"}
    with _lock:
        settings = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        for key, value in changes.items():
            if key in allowed:
                settings[key] = value
        settings["poll_seconds"] = max(30, int(settings["poll_seconds"]))
        SETTINGS_PATH.write_text(json.dumps(settings, indent=2), encoding="utf-8")
        return settings


def load_profile():
    return json.loads(PROFILE_PATH.read_text(encoding="utf-8"))


def setup_environment():
    DATA.mkdir(exist_ok=True)
    BUILD_DIR.mkdir(exist_ok=True)
    BROWSER_PROFILE.mkdir(exist_ok=True)
    libs = load_settings().get("python_libs")
    if libs and libs not in sys.path:
        sys.path.append(libs)
    try:
        import truststore
        truststore.inject_into_ssl()
    except ImportError:
        pass

import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import requests

from .filters import assess, clean_html, detect_ats, is_india, title_ok

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) JobDashboard/1.0", "Accept-Language": "en-US"}
TIMEOUT = 25
PRETTY = {
    "razorpaysoftwareprivatelimited": "Razorpay", "hackerrank": "HackerRank", "inmobi": "InMobi", "mongodb": "MongoDB",
    "gitlab": "GitLab", "openai": "OpenAI", "clickhouse": "ClickHouse", "fampay": "FamPay", "cred": "CRED",
}

_session = requests.Session()
_session.headers.update(HEADERS)
_rejected = set()
_last_run = {}
# Microsoft returns HTTP 429 when polled aggressively; Google pages are heavy.
MIN_INTERVAL = {"Microsoft": 240, "Google": 90}


def _get(url, **kwargs):
    for attempt in range(3):
        response = _session.get(url, timeout=TIMEOUT, **kwargs)
        if response.status_code != 429:
            break
        time.sleep(2 * (attempt + 1))
    response.raise_for_status()
    return response


def _iso(value):
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        seconds = value / 1000 if value > 10**11 else value
        return datetime.fromtimestamp(seconds, timezone.utc).isoformat(timespec="seconds")
    return str(value)


def _pretty(board):
    return PRETTY.get(board, board.replace("-", " ").title())


def _build(source, key, company, title, location, url, apply_url, posted, description, max_years):
    eligible, exp, fit = assess(title, location, description, max_years)
    if not eligible:
        _rejected.add(f"{source}:{key}")
        return None
    return {
        "id": f"{source}:{key}", "source": source, "company": company, "title": title.strip(),
        "location": location, "url": url, "apply_url": apply_url or url, "posted_at": _iso(posted),
        "description": description[:20000], "exp_min": exp, "fit": fit, "ats": detect_ats(apply_url or url),
    }


def _details(items, fetch, workers=8):
    """Run fetch(item) in parallel, dropping failures."""
    results = []
    with ThreadPoolExecutor(workers) as pool:
        for result in pool.map(lambda item: _safe(fetch, item), items):
            if result:
                results.append(result)
    return results


def _safe(fn, *args):
    try:
        return fn(*args)
    except Exception:
        return None


def google(known, max_years, pages=3):
    jobs, seen = [], set()
    for query in ("software engineer", "software developer"):
        for page in range(1, pages + 1):
            text = _get(
                "https://www.google.com/about/careers/applications/jobs/results/",
                params={"location": "India", "target_level": "EARLY", "q": query, "sort_by": "date", "page": page},
            ).text
            match = re.search(r"AF_initDataCallback\(\{key: 'ds:1'.*?data:(\[.*?\]), sideChannel", text, re.S)
            if not match:
                break
            items = json.loads(match.group(1))[0] or []
            if not items:
                break
            for item in items:
                key = str(item[0])
                if key in seen or f"google:{key}" in known:
                    continue
                seen.add(key)
                title = item[1]
                location = "; ".join(loc[0] for loc in (item[9] or []) if loc)
                description = clean_html(" \n".join(
                    (part[1] or "") for part in (item[4], item[3], item[10]) if isinstance(part, list) and len(part) > 1
                ))
                slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
                url = f"https://www.google.com/about/careers/applications/jobs/results/{key}-{slug}"
                posted = item[12][0] if isinstance(item[12], list) and item[12] else None
                job = _build("google", key, "Google", title, location, url, url, posted, description, max_years)
                if job:
                    jobs.append(job)
    return jobs


def microsoft(known, max_years, pages=3):
    candidates = {}
    for query in ("software engineer", "new graduate"):
        for page in range(pages):
            data = _get(
                "https://apply.careers.microsoft.com/api/pcsx/search",
                params={"domain": "microsoft.com", "query": query, "location": "India", "start": page * 10,
                        "sort_by": "timestamp"},
            ).json()["data"]
            positions = data.get("positions") or []
            for position in positions:
                key = str(position["id"])
                location = "; ".join(position.get("standardizedLocations") or position.get("locations") or [])
                if f"microsoft:{key}" in known or not title_ok(position["name"]) or not is_india(location):
                    continue
                candidates[key] = (position, location)
            if len(positions) < 10:
                break

    def detail(entry):
        position, location = entry
        key = str(position["id"])
        info = _get(
            "https://apply.careers.microsoft.com/api/pcsx/position_details",
            params={"position_id": key, "domain": "microsoft.com", "hl": "en"},
        ).json()["data"]
        url = info.get("publicUrl") or f"https://apply.careers.microsoft.com/careers/job/{key}"
        return _build("microsoft", key, "Microsoft", position["name"], location, url, url,
                      position.get("postedTs"), clean_html(info.get("jobDescription", "")), max_years)

    return _details(list(candidates.values()), detail, workers=3)


def amazon(known, max_years):
    jobs, seen = [], set()
    for query in ("software development engineer", "software engineer", "sde"):
        data = _get(
            "https://www.amazon.jobs/en/search.json",
            params={"base_query": query, "loc_query": "India", "country": "IND", "result_limit": 100, "sort": "recent"},
        ).json()
        for item in data.get("jobs", []):
            key = str(item.get("id_icims") or item.get("id"))
            if key in seen or f"amazon:{key}" in known or item.get("is_intern"):
                continue
            seen.add(key)
            description = clean_html(f"{item.get('basic_qualifications', '')}\n{item.get('description', '')}\n"
                                     f"{item.get('preferred_qualifications', '')}")
            posted = None
            try:
                posted = datetime.strptime(item["posted_date"], "%B %d, %Y").replace(tzinfo=timezone.utc).isoformat()
            except (KeyError, ValueError):
                pass
            job = _build("amazon", key, "Amazon", item["title"], item.get("normalized_location") or item.get("location"),
                         "https://www.amazon.jobs" + item["job_path"], item.get("url_next_step"), posted,
                         description, max_years)
            if job:
                jobs.append(job)
    return jobs


def greenhouse(board, known, max_years):
    data = _get(f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs").json()
    candidates = []
    for item in data.get("jobs", []):
        location = (item.get("location") or {}).get("name", "")
        key = f"{board}-{item['id']}"
        if f"greenhouse:{key}" in known or not title_ok(item["title"]) or not is_india(location):
            continue
        candidates.append(item)

    def detail(item):
        info = _get(f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs/{item['id']}").json()
        location = (item.get("location") or {}).get("name", "")
        apply_url = f"https://job-boards.greenhouse.io/{board}/jobs/{item['id']}"
        return _build("greenhouse", f"{board}-{item['id']}", _pretty(board), item["title"],
                      location, item.get("absolute_url") or apply_url, apply_url,
                      item.get("first_published") or item.get("updated_at"), clean_html(info.get("content", "")),
                      max_years)

    return _details(candidates, detail, workers=6)


def lever(board, known, max_years):
    jobs = []
    for item in _get(f"https://api.lever.co/v0/postings/{board}", params={"mode": "json"}).json():
        categories = item.get("categories") or {}
        location = "; ".join(categories.get("allLocations") or [categories.get("location", "")])
        key = f"{board}-{item['id']}"
        if f"lever:{key}" in known:
            continue
        lists = " \n".join(f"{entry.get('text', '')}\n{entry.get('content', '')}" for entry in item.get("lists") or [])
        description = clean_html(f"{item.get('descriptionPlain', '')}\n{lists}\n{item.get('additionalPlain', '')}")
        job = _build("lever", key, _pretty(board), item["text"], location, item["hostedUrl"],
                     item.get("applyUrl") or item["hostedUrl"] + "/apply", item.get("createdAt"), description,
                     max_years)
        if job:
            jobs.append(job)
    return jobs


def ashby(board, known, max_years):
    data = _get(f"https://api.ashbyhq.com/posting-api/job-board/{board}").json()
    jobs = []
    for item in data.get("jobs", []):
        if not item.get("isListed", True):
            continue
        locations = [item.get("location") or ""] + [loc.get("location", "") for loc in item.get("secondaryLocations") or []]
        country = ((item.get("address") or {}).get("postalAddress") or {}).get("addressCountry") or ""
        location = "; ".join(filter(None, locations + ([country] if country else [])))
        key = f"{board}-{item['id']}"
        if f"ashby:{key}" in known:
            continue
        job = _build("ashby", key, _pretty(board), item["title"], location, item["jobUrl"],
                     item.get("applyUrl") or item["jobUrl"] + "/application", item.get("publishedAt"),
                     item.get("descriptionPlain") or clean_html(item.get("descriptionHtml", "")), max_years)
        if job:
            jobs.append(job)
    return jobs


def all_fetchers(settings):
    sources = settings.get("sources", {})
    fetchers = []
    for name, fn in (("Google", google), ("Microsoft", microsoft), ("Amazon", amazon)):
        if sources.get(name.lower()):
            fetchers.append((name, fn))
    for kind, fn in (("greenhouse", greenhouse), ("lever", lever), ("ashby", ashby)):
        for board in sources.get(kind, []):
            fetchers.append((f"{_pretty(board)} ({kind})", lambda known, max_years, fn=fn, board=board: fn(board, known, max_years)))
    return fetchers


def scan(settings, known):
    """Fetch every source in parallel. Returns (jobs, per-source report)."""
    max_years = settings.get("max_experience_years", 2)
    known = set(known) | _rejected
    jobs, report = [], []

    def run(entry):
        name, fn = entry
        started = time.time()
        wait = MIN_INTERVAL.get(name, 0) - (started - _last_run.get(name, 0))
        if wait > 0:
            return name, [], f"rate-limited locally; next check in {int(wait)}s", 0.0
        _last_run[name] = started
        try:
            found = fn(known, max_years)
            return name, found, None, time.time() - started
        except Exception as exc:
            return name, [], f"{type(exc).__name__}: {str(exc)[:160]}", time.time() - started

    with ThreadPoolExecutor(12) as pool:
        for name, found, error, elapsed in pool.map(run, all_fetchers(settings)):
            jobs.extend(found)
            report.append({"source": name, "found": len(found), "error": error, "seconds": round(elapsed, 1)})
    return jobs, report

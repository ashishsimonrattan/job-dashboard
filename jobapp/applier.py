"""Browser automation for job applications.

Rules enforced here:
* Only jobs the user explicitly clicked "Apply" on are ever processed.
* CAPTCHAs, sign-in walls, and unanswered required questions are never bypassed: the job is marked
  for manual action and the pre-filled tab is left open for the user.
* A job is only marked "submitted" when the portal shows a confirmation message.
"""
import queue
import re
import threading
import time
from pathlib import Path

from . import db, tailor, tracker
from .config import BROWSER_PROFILE, load_profile, load_settings

FIELD_RULES = [
    (r"first\s*name|given\s*name|forename", "first_name"),
    (r"last\s*name|surname|family\s*name", "last_name"),
    (r"preferred\s*(first\s*)?name|nickname", "first_name"),
    (r"^\s*(full\s*|legal\s*|your\s*)?name\s*\*?\s*$|full\s*name|legal\s*name", "full_name"),
    (r"e-?mail", "email"),
    (r"phone|mobile|contact\s*(number|no)", "phone"),
    (r"linked\s*in", "linkedin"),
    (r"git\s*hub", "github"),
    (r"portfolio|personal\s*(web)?site|^website|other\s*website|blog", "website"),
    (r"current\s*(company|employer|organi[sz]ation)|^\s*(company|employer|organization)\s*\*?\s*$|most recent (company|employer)", "current_company"),
    (r"current\s*(job\s*)?(title|role|designation|position)|^\s*(job\s*)?title\s*\*?\s*$", "current_title"),
    (r"school|university|college|institut", "school"),
    (r"degree|qualification|education level", "degree"),
    (r"graduat\w*\s*(year|date)|year\s*of\s*graduation|passing\s*year|batch", "graduation_year"),
    (r"(total|how many)?\s*years?\s*of\s*(professional\s*|relevant\s*|work\s*|industry\s*)?experience|total\s*(work\s*|professional\s*)?experience|years?\s*of\s*exp\b|\bexp(erience)?\s*\(?(in\s*)?(years|yrs)|^\s*(work\s*)?experience\s*(in years)?\s*[*✱]?\s*$", "years_experience"),
    (r"notice\s*period|when can you (start|join)|earliest start", "notice_period"),
    (r"current\s*(ctc|compensation|salary|pay)", "current_ctc"),
    (r"expected\s*(ctc|compensation|salary|pay)|salary\s*expectation|desired\s*(salary|compensation)", "expected_ctc"),
    (r"authori[sz]ed\s*to\s*work|work\s*authori[sz]ation|legally\s*(eligible|authori[sz]ed|permitted)|right\s*to\s*work", "work_authorization_india"),
    (r"sponsor", "requires_sponsorship"),
    (r"how\s*did\s*you\s*(hear|find|learn)|where did you (hear|find)|referral source|^source", "how_did_you_hear"),
    (r"(current\s*)?location|city|where\s*are\s*you\s*(currently\s*)?(based|located)|reside", "location"),
]
VOLUNTARY = re.compile(r"gender|race|ethnic|veteran|disabilit|hispanic|latino|sexual orientation|pronoun|transgender", re.I)
DECLINE = re.compile(r"decline|prefer not|don.?t wish|not to (say|disclose|answer|self)|choose not", re.I)
# Privacy-notice acknowledgements required by many ATS forms. Only answered when the user enabled
# accept_privacy_acknowledgements in profile.json (the Apply dialog states this).
ACK = re.compile(r"privacy (policy|notice)|by clicking|acknowledg|consent to|data (protection|processing)|i (have )?read and", re.I)
ACK_OPTION = re.compile(r"acknowledge|\bagree|accept|i confirm|^yes\b|i have read|i understand", re.I)
CONFIRMATION = re.compile(
    r"thank(s| you)[^.]{0,80}(appl|submi|interest)|application (has been |was )?(received|submitted)|"
    r"we('ve| have) received your application|successfully (submitted|applied)|you('ve| have) (successfully )?applied|"
    r"application submitted|we('ll| will) be in touch",
    re.I,
)
SIGN_IN = re.compile(r"sign in|log ?in|create (an )?account|continue with google", re.I)
SUBMIT_TEXT = re.compile(r"^\s*(submit( application| your application)?|apply( now)?|send application)\s*$", re.I)

COLLECT_JS = r"""
() => {
  const txt = e => ((e && (e.innerText || e.textContent)) || '').replace(/\s+/g, ' ').trim();
  const visible = e => { const r = e.getBoundingClientRect(); const s = getComputedStyle(e);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; };
  const labelOf = el => {
    let t = '';
    if (el.labels && el.labels.length) t = [...el.labels].map(txt).join(' ');
    if (!t && el.getAttribute('aria-labelledby'))
      t = el.getAttribute('aria-labelledby').split(/\s+/).map(id => txt(document.getElementById(id))).join(' ');
    if (!t) t = el.getAttribute('aria-label') || '';
    let p = el.parentElement;
    for (let k = 0; k < 5 && p && !t; k++, p = p.parentElement) {
      const l = p.querySelector('label, legend, [class*="label"], [class*="question"]');
      if (l && !l.contains(el)) t = txt(l);
    }
    return (t || el.placeholder || el.name || el.id || '').slice(0, 300);
  };
    const out = []; let i = 0;
    const questionOf = el => {
      const fs = el.closest('fieldset');
      if (fs && fs.querySelector('legend')) return txt(fs.querySelector('legend'));
      let p = (el.closest('label') || el).parentElement;
      for (let k = 0; k < 6 && p; k++, p = p.parentElement) {
        const cands = [...p.querySelectorAll('legend, [class*="label"], [class*="question"], label, h3, h4, p, span, div')]
          .filter(c => !c.querySelector('input, select, textarea') && !c.closest('label') && txt(c));
        if (cands.length) return txt(cands[0]);
      }
      return '';
    };
  for (const el of document.querySelectorAll('input, textarea, select')) {
    const type = (el.type || el.tagName).toLowerCase();
    if (['hidden', 'submit', 'button', 'image', 'reset', 'search'].includes(type)) continue;
    if (type !== 'file' && !visible(el)) continue;
    el.setAttribute('data-ja', String(i));
    const question = (type === 'radio' || type === 'checkbox') ? questionOf(el) : '';
    const label = labelOf(el);
    out.push({ idx: i, tag: el.tagName.toLowerCase(), type, label, question, name: el.name || '', id: el.id || '',
      role: el.getAttribute('role') || '', value: (type === 'checkbox' || type === 'radio') ? (el.checked ? 'on' : '') : (el.value || ''),
      required: !!(el.required || el.getAttribute('aria-required') === 'true' || /\*\s*$/.test(label) || /\*\s*$/.test(question)),
      options: el.tagName === 'SELECT' ? [...el.options].map(o => o.text.trim()) : [] });
    i++;
  }
  return out;
}
"""


class ManualAction(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def resume_target(job):
    settings = load_settings()
    folder = Path(settings["resume_output_dir"])
    company = tailor.safe_name(job["company"])
    path = folder / f"Ashish_Simon_Rattan_{company}_resume.pdf"
    taken = {row["resume_path"] for row in db.list_jobs() if row["id"] != job["id"] and row.get("resume_path")}
    if str(path) in taken:
        path = folder / f"Ashish_Simon_Rattan_{company}_{tailor.safe_name(job['title'])[:40]}_resume.pdf"
    return path


def answer_for(label, profile):
    if ACK.search(label) and len(label) > 20:
        return ("privacy_ack", "yes") if profile.get("accept_privacy_acknowledgements") else (None, "")
    for pattern, key in FIELD_RULES:
        if re.search(pattern, label, re.I):
            return key, (profile.get(key) or "").strip()
    return None, ""


def choose_option(key, value, options):
    """Pick the option that truthfully matches value; None when unsure."""
    options = [o for o in options if o and o.strip() and not re.match(r"^(select|choose|--)", o.strip(), re.I)]
    if key == "privacy_ack":
        return next((o for o in options if ACK_OPTION.search(o)), None)
    if not value:
        return None
    wanted = value.strip().lower()
    for option in options:
        if option.strip().lower() == wanted:
            return option
    for option in options:
        if option.strip().lower().startswith(wanted):
            return option
    if key == "years_experience":
        years = float(wanted)
        for option in options:
            text = option.lower()
            below = re.search(r"(less than|under|below|<|up ?to)\s*(\d+)", text)
            if below and years < float(below.group(2)):
                return option
            span = re.search(r"(\d+)\s*(?:-|–|to)\s*(\d+)", text)
            if span and float(span.group(1)) <= years <= float(span.group(2)):
                return option
        return None
    if len(wanted) > 3:
        return next((o for o in options if wanted in o.lower()), None)
    return None


class Applier:
    def __init__(self):
        self.queue = queue.Queue()
        self.current = None
        self._playwright = None
        self._context = None
        self._thread = threading.Thread(target=self._run, name="applier", daemon=True)
        self._thread.start()

    def enqueue(self, job_ids):
        added = []
        for job_id in job_ids:
            job = db.get(job_id)
            if not job or job["status"] in ("submitted", "queued", "tailoring", "applying"):
                continue
            db.set_status(job_id, "queued", "Waiting for the application worker")
            self.queue.put(job_id)
            added.append(job_id)
        return added

    def open_manual(self, job_id):
        self.queue.put(("open", job_id))

    def pending(self):
        return self.queue.qsize()

    # ---- worker thread -------------------------------------------------
    def _run(self):
        while True:
            item = self.queue.get()
            try:
                if isinstance(item, tuple):
                    self._open_tab(db.get(item[1]))
                else:
                    self._process(item)
            except Exception as exc:  # keep the worker alive no matter what
                db.log(item if isinstance(item, str) else item[1], "error", f"Worker error: {exc}")
            finally:
                self.current = None

    def _browser(self):
        if self._context:
            try:
                _ = self._context.pages
                self._context.new_page().close()
                return self._context
            except Exception:
                self._context = None
        if not self._playwright:
            from playwright.sync_api import sync_playwright
            self._playwright = sync_playwright().start()
        settings = load_settings()
        options = dict(user_data_dir=str(BROWSER_PROFILE), headless=False, viewport=None,
                       args=["--start-maximized"], accept_downloads=True)
        try:
            self._context = self._playwright.chromium.launch_persistent_context(
                channel=settings.get("browser_channel") or None, **options)
        except Exception:
            self._context = self._playwright.chromium.launch_persistent_context(**options)
        return self._context

    def _open_tab(self, job):
        if not job:
            return
        page = self._browser().new_page()
        page.goto(job["apply_url"] or job["url"], wait_until="domcontentloaded", timeout=60000)
        page.bring_to_front()
        db.log(job["id"], "info", "Opened application page in the automation browser")

    def _process(self, job_id):
        job = db.get(job_id)
        if not job or job["status"] != "queued":
            return
        self.current = job_id
        db.set_status(job_id, "tailoring", "Building a one-page tailored résumé")
        try:
            target = resume_target(job)
            summary = tailor.build(job, target)
            db.set_status(job_id, "tailoring", f"Résumé ready ({summary})", resume_path=str(target))
            job = db.get(job_id)
        except Exception as exc:
            db.set_status(job_id, "failed", f"Résumé build failed: {exc}")
            return

        db.set_status(job_id, "applying", f"Opening {job['ats']} application form")
        try:
            confirmation = self._apply(job)
        except ManualAction as action:
            db.set_status(job_id, action.status, str(action))
            note = tracker.record(job, "Needs manual action" if action.status == "needs_manual" else "Blocked",
                                  comments=f"Dashboard: {action}")
            if note:
                db.log(job_id, "error", note)
            return
        except Exception as exc:
            db.set_status(job_id, "failed", f"Automation error: {type(exc).__name__}: {str(exc)[:300]}")
            return

        db.set_status(job_id, "submitted", "Portal confirmed the submission", confirmation=confirmation)
        note = tracker.record(job, "Submitted", confirmation=confirmation, comments="Submitted from job dashboard")
        if note:
            db.log(job_id, "error", note)

    # ---- form handling -------------------------------------------------
    def _apply(self, job):
        ats = job["ats"]
        url = job["apply_url"] or job["url"]
        context = self._browser()
        page = context.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        page.bring_to_front()
        try:
            page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass

        if ats in ("google", "microsoft", "amazon", "workday", "yc", "smartrecruiters"):
            raise ManualAction("needs_manual", f"{ats.title()} requires signing in to its own candidate portal. "
                                               f"The page is open in the automation browser; résumé: {Path(job['resume_path']).name}")
        if ats == "other":
            raise ManualAction("needs_manual", "Unknown application system; page opened for manual completion")

        self._open_form(page, ats)
        self._check_blockers(page)
        missing = self._fill(page, job)
        if missing:
            raise ManualAction("needs_manual", "Pre-filled. Please answer: " + "; ".join(missing[:8]))
        if not load_settings().get("auto_submit", True):
            raise ManualAction("needs_manual", "Review mode: form fully pre-filled — review and press Submit in the open tab")

        before = page.url
        self._click_submit(page)
        return self._await_confirmation(page, before)

    def _open_form(self, page, ats):
        if ats == "lever" and "/apply" not in page.url:
            page.goto(page.url.rstrip("/") + "/apply", wait_until="domcontentloaded")
        if ats == "ashby":
            tab = page.get_by_role("tab", name=re.compile("application", re.I))
            if tab.count():
                tab.first.click()
        if ats == "greenhouse":
            button = page.get_by_role("button", name=re.compile(r"^apply", re.I))
            if button.count() and button.first.is_visible():
                button.first.click()
        if ats == "workable":
            button = page.get_by_role("button", name=re.compile(r"apply for this job", re.I))
            if button.count() and button.first.is_visible():
                button.first.click()
        page.wait_for_timeout(1500)

    def _check_blockers(self, page):
        for frame in page.frames:
            src = frame.url or ""
            if any(marker in src for marker in ("recaptcha/api2/bframe", "recaptcha/enterprise/bframe", "hcaptcha.com/captcha",
                                                 "challenges.cloudflare.com")):
                try:
                    box = frame.frame_element().bounding_box()
                except Exception:
                    box = None
                if box and box["height"] > 60:
                    raise ManualAction("blocked", "CAPTCHA / human verification shown — please complete it in the open tab")
        body = page.inner_text("body")[:4000] if page.locator("body").count() else ""
        if not page.locator("input[type=file], input[type=email]").count() and SIGN_IN.search(body):
            raise ManualAction("needs_manual", "Portal requires sign-in — log in once in the automation browser, then retry")
        if re.search(r"no longer (accepting|available|open)|position (has been )?(filled|closed)|job not found|page not found", body, re.I):
            raise ManualAction("blocked", "Posting appears closed or unavailable")

    def _fill(self, page, job):
        profile = load_profile()
        fields = page.evaluate(COLLECT_JS)
        if not fields:
            raise ManualAction("needs_manual", "No application form found on the page")

        files = [f for f in fields if f["type"] == "file"
                 and not re.search(r"cover|autofill|transcript", f"{f['label']} {f['name']} {f['id']}", re.I)]
        resume_inputs = [f for f in files if re.search(r"resume|\bcv\b", f"{f['label']} {f['name']} {f['id']}", re.I)]
        target = (resume_inputs or files[:1])
        if target:
            page.locator(f"[data-ja='{target[0]['idx']}']").set_input_files(job["resume_path"])
            page.wait_for_timeout(3000)  # ATS résumé parsers may autofill fields; fill afterwards

        groups = {}
        for field in fields:
            if field["type"] == "radio":
                groups.setdefault(field["name"] or field["question"], []).append(field)

        for name, options in groups.items():
            question = options[0]["question"] or options[0]["label"]
            labels = [o["label"] for o in options]
            if VOLUNTARY.search(question):
                choice = next((l for l in labels if DECLINE.search(l)), None) if profile.get("decline_voluntary_disclosures") else None
            else:
                key, value = answer_for(question, profile)
                choice = choose_option(key, value, labels)
            if choice:
                self._try(job, question, lambda: page.locator(f"[data-ja='{options[labels.index(choice)]['idx']}']").check(force=True))

        for field in fields:
            if field["type"] in ("file", "radio"):
                continue
            locator = page.locator(f"[data-ja='{field['idx']}']")
            label = field["label"]
            if field["type"] == "checkbox":
                text = f"{field['question']} {label}"
                if ACK.search(text) and profile.get("accept_privacy_acknowledgements"):
                    self._try(job, label, lambda: locator.check(force=True))
                continue
            if field["tag"] == "select":
                if VOLUNTARY.search(label):
                    option = next((o for o in field["options"] if DECLINE.search(o)), None) \
                        if profile.get("decline_voluntary_disclosures") else None
                else:
                    key, value = answer_for(label, profile)
                    option = choose_option(key, value, field["options"])
                if option:
                    self._try(job, label, lambda: locator.select_option(label=option))
                continue
            if field["value"].strip():
                continue
            if field["role"] == "combobox":
                self._try(job, label, lambda: self._fill_combobox(page, locator, label, profile))
                continue
            key, value = answer_for(label, profile)
            if value and key != "privacy_ack":
                self._try(job, label, lambda: locator.fill(value))
        page.wait_for_timeout(800)
        return self._missing(page)

    @staticmethod
    def _try(job, label, action):
        try:
            action()
        except Exception as exc:
            db.log(job["id"], "warn", f"Could not fill '{label[:60]}': {str(exc)[:120]}")

    def _fill_combobox(self, page, locator, label, profile):
        if VOLUNTARY.search(label):
            if not profile.get("decline_voluntary_disclosures"):
                return
            key, value = "voluntary", ""
        else:
            key, value = answer_for(label, profile)
            if not value:
                return
        locator.click()
        if key in ("location", "current_company", "school"):
            locator.fill(value.split(",")[0])
        page.wait_for_timeout(1200)
        options = page.get_by_role("option")
        texts = [t.strip() for t in options.all_inner_texts()]
        if key == "voluntary":
            choice = next((t for t in texts if DECLINE.search(t)), None)
        else:
            choice = choose_option(key, value.split(",")[0] if key == "location" else value, texts)
        if choice:
            options.nth(texts.index(choice)).click()
        else:
            page.keyboard.press("Escape")

    def _missing(self, page):
        fields = page.evaluate(COLLECT_JS)
        groups, missing = {}, []
        for field in fields:
            if field["type"] in ("radio", "checkbox"):
                key = field["name"] or field["question"]
                entry = groups.setdefault(key, {"required": False, "checked": False, "label": field["question"] or field["label"]})
                entry["required"] |= field["required"]
                entry["checked"] |= field["value"] == "on"
            elif field["type"] == "file":
                continue
            elif field["required"] and not field["value"].strip():
                missing.append(field["label"] or field["name"])
        missing += [g["label"] or name for name, g in groups.items() if g["required"] and not g["checked"]]
        if not page.locator("input[type=file]").count() and not re.search(r"resume|cv", page.inner_text("body"), re.I):
            missing.append("Résumé upload (no upload field found)")
        return [re.sub(r"\s+", " ", m).strip()[:80] for m in missing]

    def _click_submit(self, page):
        candidates = page.locator("button[type=submit], input[type=submit], button")
        for index in range(candidates.count()):
            button = candidates.nth(index)
            try:
                if not button.is_visible():
                    continue
                text = button.inner_text() if button.evaluate("e => e.tagName") == "BUTTON" else button.get_attribute("value")
                if SUBMIT_TEXT.search(text or ""):
                    button.scroll_into_view_if_needed()
                    button.click()
                    return
            except Exception:
                continue
        raise ManualAction("needs_manual", "Pre-filled, but no Submit button was found — please submit in the open tab")

    def _await_confirmation(self, page, before):
        deadline = time.time() + 30
        while time.time() < deadline:
            page.wait_for_timeout(1500)
            try:
                body = page.inner_text("body")
            except Exception:
                continue
            match = CONFIRMATION.search(body)
            if match:
                start = max(0, match.start() - 40)
                return re.sub(r"\s+", " ", body[start:match.end() + 120]).strip()
            if re.search(r"/(confirmation|thank[s-]?you|thanks|success)", page.url, re.I) and page.url != before:
                return f"Redirected to confirmation page {page.url}"
            self._check_blockers(page)
        errors = page.locator("[class*='error'], [role='alert'], [aria-invalid='true']")
        detail = ""
        if errors.count():
            detail = "; ".join(t.strip() for t in errors.all_inner_texts() if t.strip())[:300]
        raise ManualAction("needs_manual", "Clicked Submit but no confirmation appeared. "
                           + (f"Portal says: {detail}" if detail else "Please check the open tab before retrying."))

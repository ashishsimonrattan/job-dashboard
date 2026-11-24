import html
import re

INDIA = re.compile(
    r"\b(india|bengaluru|bangalore|hyderabad|pune|gurgaon|gurugram|noida|mumbai|chennai|new delhi|delhi|kolkata|"
    r"ahmedabad|jaipur|chandigarh|kochi|trivandrum|thiruvananthapuram|coimbatore|indore)\b|,\s*(ind|in)\s*(,|$)",
    re.I,
)
ENGINEERING = re.compile(
    r"\b(software|sde|swe|developer|programmer|engineer(ing)?|member of technical staff|mts|backend|back-end|"
    r"frontend|front-end|full[\s-]?stack|platform|devops|sre|reliability|data engineer|ml engineer)\b",
    re.I,
)
NON_SOFTWARE = re.compile(
    r"\b(sales|support|solutions?|customer|field|pre-?sales|account|marketing|recruit\w*|legal|finance|"
    r"hardware|mechanical|electrical|civil|analog|rf|silicon|asic|physical design|verification|technical writer|"
    r"program manager|product manager|design(er)?|security analyst|consultant|business|operations|"
    r"escalation|implementation|deployment strategist|forward deployed)\b",
    re.I,
)
SENIOR = re.compile(
    r"\b(senior|sr\.?|staff|principal|lead|leader|manager|director|head|architect|vp|president|"
    r"distinguished|fellow|iii|iv|v|3|4|5|intern|internship|apprentice|co-?op)\b|\b(sde|swe|l)[\s-]?([3-9]|iii|iv)\b",
    re.I,
)
LEVEL_TWO = re.compile(r"\b(ii|2)\b|\b(sde|swe)[\s-]?(2|ii)\b", re.I)
ENTRY = re.compile(
    r"\b(new grad\w*|graduate|fresher|freshers|entry[\s-]level|junior|jr\.?|associate|early career|campus|"
    r"university|sde[\s-]?(i|1)|software (development )?engineer[\s-]?(i|1)|0\s*[-–to]+\s*[12] years?)\b",
    re.I,
)
YEARS = re.compile(
    r"(?<![\d.])(\d{1,2})(?:\.\d)?\s*(?:\+|plus)?\s*(?:(?:-|–|to)\s*(\d{1,2})\s*)?\+?\s*(?:years?|yrs?)\b"
    r"(?:\s+(?:of|in|with))?[^.;\n]{0,80}?\b(experience|exp)\b",
    re.I,
)


def clean_html(text):
    if not text:
        return ""
    text = html.unescape(html.unescape(text))
    text = re.sub(r"<(br|/p|/li|/h\d|/div)[^>]*>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    return re.sub(r"\n\s*\n+", "\n", text).strip()


def is_india(location):
    return any(INDIA.search(part.strip()) for part in re.split(r"[;|]", location or ""))


def title_ok(title):
    return bool(ENGINEERING.search(title)) and not NON_SOFTWARE.search(title) and not SENIOR.search(title)


def min_experience(text):
    """Smallest required-years figure mentioned next to the word 'experience'."""
    values = []
    for match in YEARS.finditer(text or ""):
        low = int(match.group(1))
        if low <= 15:
            values.append(low)
    return min(values) if values else None


def assess(title, location, description, max_years=2):
    """Returns (eligible, exp_min, fit)."""
    if not is_india(location) or not title_ok(title):
        return False, None, None
    exp = min_experience(description)
    if exp is not None and exp > max_years:
        return False, exp, None
    if ENTRY.search(title) or (not LEVEL_TWO.search(title) and ENTRY.search((description or "")[:3000])):
        fit = "entry"
    elif LEVEL_TWO.search(title) and (exp is None or exp >= 2):
        fit = "stretch"
    elif exp is not None:
        fit = "match"
    else:
        fit = "unknown"
    return True, exp, fit


def detect_ats(url):
    url = (url or "").lower()
    for marker, name in (
        ("greenhouse.io", "greenhouse"), ("gh_jid=", "greenhouse"), ("lever.co", "lever"), ("ashbyhq.com", "ashby"),
        ("workable.com", "workable"), ("myworkdayjobs", "workday"), ("amazon.jobs", "amazon"),
        ("careers.microsoft.com", "microsoft"), ("google.com/about/careers", "google"),
        ("workatastartup.com", "yc"), ("ycombinator.com", "yc"), ("smartrecruiters", "smartrecruiters"),
    ):
        if marker in url:
            return name
    return "other"

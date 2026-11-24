"""Builds a one-page tailored résumé from verified LaTeX content.

Only the Profile text and the ordering/selection of verified skills change; experience bullets,
section order, and layout stay exactly as in resume/base_resume.tex.
"""
import re
import shutil
import subprocess

from .config import BUILD_DIR, RESUME_DIR, load_settings

PROFILES = {
    "backend": r"""\textbf{Software Development Engineer} building low-latency backend APIs, event-driven services, and production alerts.
  Built Python financial-incident detection, Kafka pipelines, and 100+ SLA-based Splunk alerts.
  Cut API latency from approximately 5s to 150ms and built a full-stack platform with Spring Boot, PostgreSQL, and Next.js.""",
    "java": r"""\textbf{Software Development Engineer} with Java/Spring Boot, REST API, microservices, and cloud-native
  experience. Built a Spring Boot platform with 30+ domain services and approximately 200 REST APIs.
  Migrated 15 legacy services to Golang microservices and deployed services using Docker and Kubernetes;
  experienced in production debugging, testing, and observability.""",
    "reliability": r"""\textbf{Software Development Engineer} focused on low-latency distributed services, production observability, and incident automation.
  Deployed Go microservices with Docker and Kubernetes, cut API latency from approximately 5s to 150ms, and reduced manual monitoring by 80\% with 100+ SLA-based Splunk alerts.
  Built Kafka event pipelines and Python production incident tooling.""",
    "distributed": r"""\textbf{Software Development Engineer} focused on distributed systems, latency, and production reliability in Go and Python.
  Migrated 15 legacy services to Go microservices, built Kafka pipelines, and reduced manual monitoring by 80\% with Splunk alerts.
  Built GTest validation suites and shipped real-time WebSocket chat for a full-stack platform.""",
    "frontend": r"""\textbf{Software Development Engineer} building production web applications with React and Next.js,
  supported by Python, Java/Spring Boot, and Golang.
  Built REST APIs and React Admin UI pages with a sub-300ms latency SLO.
  Owns a full-stack social travel platform with JWT/OTP authentication and real-time WebSocket chat.""",
    "fullstack": r"""\textbf{Software Development Engineer} building full-stack features with React, Next.js, Spring Boot, and PostgreSQL.
  Shipped 10+ REST APIs and React admin pages, automated production incident detection in Python, and deployed Go services with Docker and Kubernetes.
  Built a full-stack platform with authentication, real-time chat, and containerized services.""",
    "data": r"""\textbf{Software Development Engineer} building event-driven data pipelines and backend services.
  Built Kafka event pipelines for asynchronous processing and cross-service synchronization, and automated Python financial-incident detection over production data.
  Reduced manual monitoring by 80\% with 100+ SLA-based Splunk alerts and cut API latency from approximately 5s to 150ms.""",
}

LANGUAGES = {
    "Go": r"\bgolang\b|\bgo\b(?=\s*(?:,|/|\)|and\b|or\b|lang|programming|developer|services|microservices|backend))",
    "Python": r"\bpython\b",
    "Java": r"\bjava\b(?!\s*script)",
    "C++": r"c\+\+|\bcpp\b",
    "C": r"\bc\s*/\s*c\+\+|\bc,\s*c\+\+|\bc programming\b",
}
# React/Next.js work in the résumé implies these; they are only listed when the job asks for them.
OPTIONAL_LANGUAGES = {"JavaScript": r"\bjavascript\b|\bnode\.?js\b", "TypeScript": r"\btypescript\b"}
FRAMEWORKS = {
    "React.js": r"\breact", "Next.js": r"\bnext\.?js", "Spring Boot": r"\bspring", "GORM": r"\bgorm\b|\bgolang\b",
    "GTest": r"\bgtest\b|google ?test|unit test|testing",
}
TOOLS = {
    "AWS": r"\baws\b|amazon web services", "Docker": r"docker|container", "Kubernetes": r"kubernetes|\bk8s\b",
    "Azure": r"\bazure\b", "Splunk": r"splunk|observability|monitoring", "Grafana": r"grafana|observability|monitoring",
    "CI/CD": r"ci/cd|continuous (integration|delivery|deployment)", "Git": r"\bgit\b",
}
FOCUS = {
    "java": r"\bjava\b(?!\s*script)|\bspring\b|\bj2ee\b|\bhibernate\b",
    "frontend": r"front[\s-]?end|\breact\b|\bui\b|javascript|typescript|\bcss\b|\bhtml\b|web app",
    "fullstack": r"full[\s-]?stack",
    "reliability": r"\bsre\b|reliability|observability|on-?call|incident|kubernetes|devops|infrastructure|monitoring",
    "data": r"data engineer|\bspark\b|\betl\b|data pipeline|warehouse|\bairflow\b|\bkafka\b",
    "distributed": r"distributed systems?|\bgolang\b|scalab|concurren|low[\s-]latency",
    "backend": r"back[\s-]?end|\bapi\b|microservice|server[\s-]side",
}


def _count(pattern, text, flags=re.I):
    return len(re.findall(pattern, text, flags))


def _ordered(options, text):
    base = list(options)
    return sorted(base, key=lambda name: (-min(_count(options[name], text), 5), base.index(name)))


def choose_focus(job):
    title = job["title"].lower()
    text = f"{job['title']} {job['title']} {job['title']}\n{job.get('description') or ''}"
    if re.search(r"front[\s-]?end|\bui\b", title):
        return "frontend"
    if re.search(r"full[\s-]?stack", title):
        return "fullstack"
    if re.search(r"\bsre\b|reliability|devops|infra", title):
        return "reliability"
    if re.search(r"data engineer", title):
        return "data"
    scores = {name: _count(pattern, text) for name, pattern in FOCUS.items()}
    scores["fullstack"] += min(scores["frontend"], scores["backend"]) // 2
    best = max(scores, key=lambda name: (scores[name], name == "backend"))
    # Generic SWE descriptions mention UI work in passing; only pick frontend when it clearly dominates.
    if best == "frontend" and scores["backend"] + scores["distributed"] >= scores["frontend"] / 2:
        best = "fullstack"
    return best if scores[best] else "backend"


def skills_for(job):
    text = f"{job['title']}\n{job.get('description') or ''}"
    languages = _ordered(LANGUAGES, text)
    extras = [name for name, pattern in OPTIONAL_LANGUAGES.items() if re.search(pattern, text, re.I)]
    if extras:
        languages = languages[:2] + extras + languages[2:]
    return languages, _ordered(FRAMEWORKS, text), _ordered(TOOLS, text)


def _tex(value):
    return value.replace("&", r"\&").replace("#", r"\#").replace("%", r"\%")


def safe_name(value):
    return re.sub(r"[^A-Za-z0-9]+", "_", value).strip("_") or "Company"


def _compile(workdir, stem):
    settings = load_settings()
    result = subprocess.run(
        [settings["tectonic"], "--keep-logs", f"{stem}.tex"], cwd=workdir, capture_output=True, text=True, timeout=180,
    )
    pdf = workdir / f"{stem}.pdf"
    if result.returncode != 0 or not pdf.exists():
        raise RuntimeError(f"LaTeX build failed: {(result.stderr or result.stdout)[-400:]}")
    log = (workdir / f"{stem}.log").read_text(encoding="utf-8", errors="replace")
    import pymupdf
    with pymupdf.open(pdf) as document:
        pages = len(document)
        text = document[0].get_text()
    problems = []
    if pages != 1:
        problems.append(f"{pages} pages")
    if "Overfull" in log:
        problems.append("overfull line")
    for required in ("Ashish Simon Rattan", "ashishsimonrattan@gmail.com", "Experience", "Education"):
        if required not in text:
            problems.append(f"missing '{required}'")
    return pdf, problems


def build(job, target_path):
    """Compile a tailored résumé for job and copy it to target_path. Returns a short summary."""
    workdir = BUILD_DIR / safe_name(job["id"])
    workdir.mkdir(parents=True, exist_ok=True)
    shutil.copy(RESUME_DIR / "base_resume.tex", workdir / "base_resume.tex")
    languages, frameworks, tools = skills_for(job)
    focus = choose_focus(job)
    attempts = [focus] if focus == "backend" else [focus, "backend"]
    last_problems = []
    for profile in attempts:
        source = "\n".join([
            "\\newcommand{\\resumeProfile}{\n  " + PROFILES[profile] + "\n}",
            "\\newcommand{\\resumeLanguages}{" + _tex(", ".join(languages)) + "}",
            "\\newcommand{\\resumeFrameworks}{" + _tex(", ".join(frameworks)) + "}",
            "\\newcommand{\\resumeCloudTools}{" + _tex(", ".join(tools)) + "}",
            "\\input{base_resume.tex}",
            "",
        ])
        (workdir / "tailored.tex").write_text(source, encoding="utf-8")
        pdf, last_problems = _compile(workdir, "tailored")
        if not last_problems:
            target_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(pdf, target_path)
            return f"{profile} profile; languages: {', '.join(languages)}"
    raise RuntimeError("Résumé failed one-page validation: " + ", ".join(last_problems))

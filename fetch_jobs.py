#!/usr/bin/env python3
"""
Cyber Zone — Multi-query job + internship fetcher.
Fetches from Adzuna across IN / US / GB, standardizes, dedupes,
tags experience level, matches against user skills, saves to
data/jobs.json, and optionally emails new matches.
"""

import json
import os
import re
import sys
import smtplib
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import requests

# ------------------------------------------------------------------
# CONFIG
# ------------------------------------------------------------------
ADZUNA_APP_ID  = os.getenv("ADZUNA_APP_ID")
ADZUNA_APP_KEY = os.getenv("ADZUNA_APP_KEY")

SMTP_USER = os.getenv("SMTP_USER")          # e.g. you@gmail.com
SMTP_PASS = os.getenv("SMTP_APP_PASSWORD")  # Gmail App Password
EMAIL_TO  = os.getenv("EMAIL_TO", SMTP_USER)

BASE_DIR       = Path(__file__).parent
DATA_DIR       = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)
JOBS_FILE      = DATA_DIR / "jobs.json"
PROFILE_FILE   = BASE_DIR / "user_profile.json"

MARKETS = ["in", "us", "gb"]

QUERIES = [
    # Internships
    {"q": "Cyber Security Intern",         "type": "internship"},
    {"q": "SOC Analyst Intern",            "type": "internship"},
    {"q": "Information Security Trainee",  "type": "internship"},
    # Entry-level / freshers
    {"q": "Junior SOC Analyst",            "type": "full-time"},
    {"q": "Graduate Security Engineer",    "type": "full-time"},
    {"q": "L1 Analyst",                    "type": "full-time"},
]

ENTRY_KEYWORDS  = ["intern", "trainee", "fresher", "graduate", "junior", "entry", "l1"]
MID_KEYWORDS    = ["l2", "analyst ii", "mid", "associate", "engineer ii"]
SENIOR_KEYWORDS = ["senior", "lead", "principal", "l3", "staff", "architect", "manager"]

# ------------------------------------------------------------------
# HELPERS
# ------------------------------------------------------------------
def log(msg: str) -> None:
    print(f"[{datetime.now(timezone.utc).isoformat(timespec='seconds')}] {msg}", flush=True)


def classify_experience(title: str, description: str = "") -> str:
    text = f"{title} {description}".lower()
    if any(k in text for k in ENTRY_KEYWORDS):
        return "Entry"
    if any(k in text for k in SENIOR_KEYWORDS):
        return "Senior"
    if any(k in text for k in MID_KEYWORDS):
        return "Mid"
    return "Mid"  # default


def clean_html(raw: str) -> str:
    if not raw:
        return ""
    text = re.sub(r"<[^>]+>", " ", raw)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def load_profile() -> dict:
    if not PROFILE_FILE.exists():
        log(f"WARNING: {PROFILE_FILE} missing — using empty profile")
        return {"skills": [], "min_match_score": 1}
    with open(PROFILE_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def match_skills(job: dict, skills: list[str]) -> dict:
    haystack = f"{job['title']} {job['description']}".lower()
    matched = [s for s in skills if s.lower() in haystack]
    total = len(skills) or 1
    return {
        "matched_skills": matched,
        "match_score": len(matched),
        "match_total": total,
    }


def fetch_adzuna(query: str, country: str) -> list[dict]:
    """Fetch one query/country combo. Raises on HTTP error."""
    url = f"https://api.adzuna.com/v1/api/jobs/{country}/search/1"
    params = {
        "app_id": ADZUNA_APP_ID,
        "app_key": ADZUNA_APP_KEY,
        "results_per_page": 50,
        "what": query,
        "content-type": "application/json",
    }
    r = requests.get(url, params=params, timeout=30)
    r.raise_for_status()
    return r.json().get("results", [])


def normalize(raw: dict, country: str, job_type: str) -> dict:
    title = raw.get("title", "").strip()
    desc = clean_html(raw.get("description", ""))
    company = (raw.get("company") or {}).get("display_name", "Unknown")
    location = (raw.get("location") or {}).get("display_name", "N/A")
    salary_min = raw.get("salary_min")
    salary_max = raw.get("salary_max")

    salary = "Not disclosed"
    if salary_min and salary_max:
        salary = f"{int(salary_min):,} – {int(salary_max):,}"
    elif salary_min:
        salary = f"From {int(salary_min):,}"

    return {
        "id": f"{country}-{raw.get('id', '')}",
        "title": title,
        "company": company,
        "location": location,
        "country": country.upper(),
        "market": {
            "in": "India",
            "us": "United States",
            "gb": "United Kingdom",
        }.get(country, country.upper()),
        "remote": "remote" in f"{title} {desc}".lower(),
        "url": raw.get("redirect_url", ""),
        "created": raw.get("created", ""),
        "salary": salary,
        "description": desc[:1200],
        "job_type": job_type,
        "experience": classify_experience(title, desc),
        "source": "Adzuna",
    }


# ------------------------------------------------------------------
# EMAIL
# ------------------------------------------------------------------
def build_email_html(new_jobs: list[dict], profile: dict) -> str:
    rows = []
    for j in new_jobs:
        skills_badge = " ".join(
            f'<span style="background:#10B981;color:#0F172A;padding:2px 8px;'
            f'border-radius:4px;font-size:11px;margin-right:4px;">{s}</span>'
            for s in j.get("matched_skills", [])
        )
        rows.append(f"""
        <tr>
          <td style="padding:12px;border-bottom:1px solid #1E293B;">
            <a href="{j['url']}" style="color:#06B6D4;text-decoration:none;font-weight:600;">
              {j['title']}
            </a><br>
            <span style="color:#94A3B8;font-size:13px;">
              {j['company']} · {j['location']} · {j['market']}
            </span><br>
            <span style="color:#64748B;font-size:12px;">
              {j['job_type'].upper()} · {j['experience']} · ⚡ {j['match_score']}/{j['match_total']}
            </span><br>
            {skills_badge}
          </td>
        </tr>""")

    return f"""
    <html><body style="background:#0F172A;color:#E2E8F0;font-family:monospace;padding:24px;">
      <h1 style="color:#10B981;">&gt;_ Cyber Zone Alert</h1>
      <p style="color:#94A3B8;">{len(new_jobs)} new matches for your skill profile.</p>
      <table style="width:100%;border-collapse:collapse;background:#111827;border-radius:8px;">
        {''.join(rows)}
      </table>
      <p style="color:#64748B;font-size:12px;margin-top:24px;">
        Generated {datetime.now(timezone.utc).isoformat(timespec='seconds')}
      </p>
    </body></html>"""


def send_email(subject: str, html: str) -> None:
    if not all([SMTP_USER, SMTP_PASS, EMAIL_TO]):
        log("SMTP env vars missing — skipping email")
        return

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = SMTP_USER
    msg["To"]      = EMAIL_TO
    msg.attach(MIMEText(html, "html"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as s:
            s.login(SMTP_USER, SMTP_PASS)
            s.sendmail(SMTP_USER, [EMAIL_TO], msg.as_string())
        log(f"Email sent → {EMAIL_TO}")
    except Exception as e:
        log(f"EMAIL FAILED: {e}")


# ------------------------------------------------------------------
# MAIN
# ------------------------------------------------------------------
def main() -> int:
    if not ADZUNA_APP_ID or not ADZUNA_APP_KEY:
        log("FATAL: ADZUNA_APP_ID / ADZUNA_APP_KEY not set")
        return 1

    profile = load_profile()
    skills = profile.get("skills", [])
    min_score = profile.get("min_match_score", 1)

    # Load previous state for dedup
    previous_ids: set[str] = set()
    if JOBS_FILE.exists():
        try:
            with open(JOBS_FILE, "r", encoding="utf-8") as f:
                previous_ids = {j["id"] for j in json.load(f)}
        except Exception as e:
            log(f"Could not read previous jobs.json: {e}")

    all_jobs: dict[str, dict] = {}
    errors = 0

    for market in MARKETS:
        for q in QUERIES:
            try:
                log(f"Fetching [{market}] '{q['q']}'")
                results = fetch_adzuna(q["q"], market)
                for raw in results:
                    job = normalize(raw, market, q["type"])
                    job.update(match_skills(job, skills))
                    all_jobs[job["id"]] = job
            except requests.HTTPError as e:
                errors += 1
                log(f"  HTTP error {e.response.status_code} for {q['q']}@{market}")
            except Exception as e:
                errors += 1
                log(f"  Error: {e}")

    jobs_list = sorted(
        all_jobs.values(),
        key=lambda j: (j["match_score"], j.get("created", "")),
        reverse=True,
    )

    with open(JOBS_FILE, "w", encoding="utf-8") as f:
        json.dump(jobs_list, f, indent=2, ensure_ascii=False)

    log(f"Saved {len(jobs_list)} unique jobs ({errors} query errors)")

    # -------- New high-match jobs → email --------
    new_matches = [
        j for j in jobs_list
        if j["id"] not in previous_ids
        and j["match_score"] >= min_score
        and j["experience"] in ("Entry", "Mid")
    ]

    if new_matches:
        log(f"{len(new_matches)} new matches to email")
        send_email(
            subject=f"[Cyber Zone] {len(new_matches)} new security roles",
            html=build_email_html(new_matches, profile),
        )
    else:
        log("No new matches — no email")

    return 0


if __name__ == "__main__":
    sys.exit(main())
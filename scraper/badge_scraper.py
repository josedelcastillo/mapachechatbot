"""
Lambda: mapache-badge-scraper
Triggered daily by EventBridge. Logs in to journey.actonacademy.org,
iterates all learners, scrapes their badge progress, and saves a single
JSON file to S3. No history — overwrites on every run.

S3 output key: badge_progress/all_mapaches.json
Schema:
{
  "scraped_at": "2026-04-23T06:00:00Z",
  "learners": {
    "Jose Del Castillo": {
      "user_id": "20822",
      "approved": ["Boundaries: Una cultura...", ...],
      "in_progress": {"Nurture: Criar con respeto": 33, ...}
    },
    ...
  }
}
"""

import html as htmlmod
import json
import logging
import os
import re
import time
from datetime import datetime, timezone

import boto3
import requests
from bs4 import BeautifulSoup

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# — env vars —
S3_BUCKET = os.environ["S3_BUCKET_NAME"]
S3_KEY = os.environ.get("BADGE_PROGRESS_S3_KEY", "badge_progress/all_mapaches.json")
SCHOOL_ID = os.environ.get("JOURNEY_SCHOOL_ID", "805d5c3d")
STUDIO_ID = os.environ.get("JOURNEY_STUDIO_ID", "fc376e6b")
SEED_USER_ID = os.environ.get("JOURNEY_SEED_USER_ID", "0d33547e")
DELAY_BETWEEN_REQUESTS = float(os.environ.get("SCRAPE_DELAY_SEC", "0.5"))

BASE_URL = "https://journey.actonacademy.org"
_s3 = None
_ssm = None


def _ssm_client():
    global _ssm
    if _ssm is None:
        _ssm = boto3.client("ssm", region_name=os.environ.get("AWS_REGION_NAME", "us-east-1"))
    return _ssm


def _get_credentials() -> tuple[str, str]:
    ssm = _ssm_client()
    email = ssm.get_parameter(
        Name=os.environ["JOURNEY_EMAIL_SSM_KEY"], WithDecryption=True
    )["Parameter"]["Value"]
    password = ssm.get_parameter(
        Name=os.environ["JOURNEY_PASSWORD_SSM_KEY"], WithDecryption=True
    )["Parameter"]["Value"]
    return email, password


def _s3_client():
    global _s3
    if _s3 is None:
        _s3 = boto3.client("s3", region_name=os.environ.get("AWS_REGION_NAME", "us-east-1"))
    return _s3


def lambda_handler(event, context):
    logger.info("Badge scraper starting")
    try:
        email, password = _get_credentials()
        session = _login(email, password)
        learners = _get_learner_list(session)
        logger.info("Found %d learners", len(learners))

        results = {}
        for i, learner in enumerate(learners):
            name = learner["name"]
            try:
                url = BASE_URL + learner["href"]
                r = session.get(url, timeout=15)
                approved, in_progress = _parse_badges(r.text)
                results[name] = {
                    "user_id": learner["user_id"],
                    "approved": approved,
                    "in_progress": in_progress,
                }
                logger.info(
                    "[%d/%d] %s — %d approved, %d in_progress",
                    i + 1, len(learners), name, len(approved), len(in_progress),
                )
            except Exception:
                logger.exception("Failed to scrape learner %s", name)
                results[name] = {"user_id": learner["user_id"], "approved": [], "in_progress": {}, "error": True}

            if i < len(learners) - 1:
                time.sleep(DELAY_BETWEEN_REQUESTS)

        payload = {
            "scraped_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "learner_count": len(results),
            "learners": results,
        }
        _save_to_s3(payload)
        logger.info("Scrape complete — %d learners saved to s3://%s/%s", len(results), S3_BUCKET, S3_KEY)
        return {"status": "ok", "learner_count": len(results)}

    except Exception:
        logger.exception("Scraper failed")
        raise


def _login(email: str, password: str) -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )

    # Step 1: GET landing page to find /auth/auth0 form
    r1 = s.get(BASE_URL, timeout=15, allow_redirects=True)
    soup1 = BeautifulSoup(r1.text, "html.parser")
    auth0_form = soup1.find("form", {"action": "/auth/auth0"})
    if not auth0_form:
        raise RuntimeError("Could not find /auth/auth0 form — page structure may have changed")

    csrf = auth0_form.find("input", {"name": "authenticity_token"})["value"]

    # Step 2: POST to /auth/auth0 → redirects to Auth0 Universal Login
    r2 = s.post(
        f"{BASE_URL}/auth/auth0",
        data={"authenticity_token": csrf, "commit": "Log In"},
        allow_redirects=True,
        timeout=15,
    )
    if "auth0.com" not in r2.url:
        raise RuntimeError(f"Expected Auth0 redirect, got: {r2.url}")

    # Step 3: POST credentials to Auth0
    auth0_soup = BeautifulSoup(r2.text, "html.parser")
    hidden = {
        inp.get("name"): inp.get("value", "")
        for inp in auth0_soup.find_all("input", {"type": "hidden"})
        if inp.get("name")
    }
    r3 = s.post(
        r2.url,
        data={**hidden, "username": email, "password": password, "action": "default"},
        allow_redirects=True,
        timeout=15,
    )
    if "auth0.com" in r3.url:
        # Log what Auth0 returned to help diagnose (captcha, bot detection, error page, etc.)
        from bs4 import BeautifulSoup as _BS
        _soup = _BS(r3.text, "html.parser")
        # Extract visible error message if present
        error_el = _soup.select_one(".error-message, [data-error], .ulp-error, #error-element-password, #error-element-username")
        error_text = error_el.get_text(strip=True) if error_el else "no visible error"
        # Also check for captcha
        has_captcha = "captcha" in r3.text.lower() or "recaptcha" in r3.text.lower()
        logger.error(
            "Auth0 login failed. Final URL: %s | Error: %s | Captcha: %s",
            r3.url, error_text, has_captcha
        )
        raise RuntimeError(f"Auth0 login failed — landed on {r3.url}")

    logger.info("Login successful, landed on: %s", r3.url)
    return s


def _get_learner_list(session: requests.Session) -> list[dict]:
    seed_url = (
        f"{BASE_URL}/schools/{SCHOOL_ID}/studios/{STUDIO_ID}"
        f"/progress/users/{SEED_USER_ID}/badge_plan/progress?guide_view=false"
    )
    r = session.get(seed_url, timeout=15)
    soup = BeautifulSoup(r.text, "html.parser")

    learners = []
    seen_ids = set()
    for item in soup.select(".learner-list .dropdown-item-wrapper"):
        a = item.select_one("a.dropdown-item-link")
        if not a:
            continue
        uid = item.get("data-user-id")
        if uid in seen_ids:
            continue
        seen_ids.add(uid)
        name = a.select_one("span").text.strip()
        learners.append({"name": name, "user_id": uid, "href": a["href"]})

    return learners


def _parse_badges(html_text: str) -> tuple[list[str], dict[str, int]]:
    soup = BeautifulSoup(html_text, "html.parser")
    approved = []
    in_progress = {}
    seen = set()

    for card in soup.select(".badge-assignment-grid-card"):
        classes = card.get("class", [])
        dc = htmlmod.unescape(card.get("data-content", ""))
        inner = BeautifulSoup(dc, "html.parser")
        h6 = inner.select_one("h6.black") or inner.select_one("h6")
        name_raw = h6.text.strip() if h6 else ""
        name = re.sub(r"^L\d+\s*-\s*", "", name_raw).strip()

        if not name or name in seen or name == "Placeholder":
            continue
        seen.add(name)

        if "approved" in classes:
            approved.append(name)
        elif "in_progress" in classes:
            pct_el = card.select_one(".progress-percent")
            in_progress[name] = int(pct_el.text.strip()) if pct_el else 0

    return approved, in_progress


def _save_to_s3(payload: dict) -> None:
    _s3_client().put_object(
        Bucket=S3_BUCKET,
        Key=S3_KEY,
        Body=json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"),
        ContentType="application/json",
    )

"""
Local runner: scrapes badge progress from journey.actonacademy.org
and uploads the result to S3.

Auth0 presents a captcha from Lambda IPs — this script runs from your
local machine where there is no captcha.

Setup (one time):
  pip install requests beautifulsoup4 boto3

Schedule with launchd (Mac) or cron:
  cron: 0 6 * * * cd /path/to/mapache-chatbot && python scraper/run_local.py

Environment variables needed:
  JOURNEY_EMAIL, JOURNEY_PASSWORD, S3_BUCKET_NAME
  AWS credentials via ~/.aws/credentials or env vars
"""

import html as htmlmod
import json
import logging
import os
import re
import sys
import time
from datetime import datetime, timezone

import boto3
import requests
from bs4 import BeautifulSoup

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

EMAIL = os.environ.get("JOURNEY_EMAIL", "j.delcastillos@gmail.com")
PASSWORD = os.environ.get("JOURNEY_PASSWORD", "pPM@VDv9G!3baBH")
S3_KEY = os.environ.get("BADGE_PROGRESS_S3_KEY", "badge_progress/all_mapaches.json")
SCHOOL_ID = os.environ.get("JOURNEY_SCHOOL_ID", "805d5c3d")
STUDIO_ID = os.environ.get("JOURNEY_STUDIO_ID", "fc376e6b")
SEED_USER_ID = os.environ.get("JOURNEY_SEED_USER_ID", "0d33547e")
DELAY = float(os.environ.get("SCRAPE_DELAY_SEC", "0.4"))
BASE_URL = "https://journey.actonacademy.org"


def _login() -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
    r1 = s.get(BASE_URL, allow_redirects=True)
    soup1 = BeautifulSoup(r1.text, "html.parser")
    auth0_form = soup1.find("form", {"action": "/auth/auth0"})
    if not auth0_form:
        raise RuntimeError("Could not find /auth/auth0 form")
    csrf = auth0_form.find("input", {"name": "authenticity_token"})["value"]

    r2 = s.post(f"{BASE_URL}/auth/auth0", data={"authenticity_token": csrf, "commit": "Log In"}, allow_redirects=True)
    if "auth0.com" not in r2.url:
        raise RuntimeError(f"Expected Auth0 redirect, got: {r2.url}")

    auth0_soup = BeautifulSoup(r2.text, "html.parser")
    hidden = {i["name"]: i.get("value", "") for i in auth0_soup.find_all("input", {"type": "hidden"}) if i.get("name")}
    r3 = s.post(r2.url, data={**hidden, "username": EMAIL, "password": PASSWORD, "action": "default"}, allow_redirects=True)

    if "auth0.com" in r3.url:
        raise RuntimeError("Auth0 login failed — check credentials")

    logger.info("Login successful")
    return s


def _get_learners(session: requests.Session) -> list[dict]:
    url = f"{BASE_URL}/schools/{SCHOOL_ID}/studios/{STUDIO_ID}/progress/users/{SEED_USER_ID}/badge_plan/progress?guide_view=false"
    soup = BeautifulSoup(session.get(url).text, "html.parser")
    learners, seen = [], set()
    for item in soup.select(".learner-list .dropdown-item-wrapper"):
        a = item.select_one("a.dropdown-item-link")
        if not a:
            continue
        uid = item.get("data-user-id")
        if uid in seen:
            continue
        seen.add(uid)
        learners.append({"name": a.select_one("span").text.strip(), "user_id": uid, "href": a["href"]})
    return learners


def _parse_badges(html_text: str) -> tuple[list[str], dict[str, int]]:
    soup = BeautifulSoup(html_text, "html.parser")
    approved, in_progress, seen = [], {}, set()
    for card in soup.select(".badge-assignment-grid-card"):
        classes = card.get("class", [])
        dc = htmlmod.unescape(card.get("data-content", ""))
        inner = BeautifulSoup(dc, "html.parser")
        h6 = inner.select_one("h6.black") or inner.select_one("h6")
        name = re.sub(r"^L\d+\s*-\s*", "", h6.text.strip() if h6 else "").strip()
        if not name or name in seen or name == "Placeholder":
            continue
        seen.add(name)
        if "approved" in classes:
            approved.append(name)
        elif "in_progress" in classes:
            pct = card.select_one(".progress-percent")
            in_progress[name] = int(pct.text.strip()) if pct else 0
    return approved, in_progress


def main():
    logger.info("Scraping badge progress for all learners...")
    session = _login()
    learners = _get_learners(session)
    logger.info("Found %d learners", len(learners))

    results = {}
    for i, learner in enumerate(learners):
        name = learner["name"]
        try:
            r = session.get(BASE_URL + learner["href"])
            approved, in_progress = _parse_badges(r.text)
            results[name] = {"user_id": learner["user_id"], "approved": approved, "in_progress": in_progress}
            logger.info("[%d/%d] %s — %d approved", i + 1, len(learners), name, len(approved))
        except Exception as e:
            logger.warning("Failed for %s: %s", name, e)
            results[name] = {"user_id": learner["user_id"], "approved": [], "in_progress": {}, "error": True}
        if i < len(learners) - 1:
            time.sleep(DELAY)

    payload = {
        "scraped_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "learner_count": len(results),
        "learners": results,
    }

    # Upload to S3
    account = boto3.client("sts").get_caller_identity()["Account"]
    bucket = os.environ.get("S3_BUCKET_NAME", f"mapache-chatbot-kb-{account}")
    boto3.client("s3").put_object(
        Bucket=bucket,
        Key=S3_KEY,
        Body=json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"),
        ContentType="application/json",
    )
    logger.info("Saved to s3://%s/%s (%d learners)", bucket, S3_KEY, len(results))
    return payload


if __name__ == "__main__":
    main()

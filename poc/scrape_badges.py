"""
POC: Scrape badge progress from journey.actonacademy.org
Validates the Auth0 login flow + HTML parsing before full integration.

Run:
  pip install requests beautifulsoup4
  python poc/scrape_badges.py
"""

import html
import json
import re
import sys

import requests
from bs4 import BeautifulSoup

EMAIL = "j.delcastillos@gmail.com"
PASSWORD = "pPM@VDv9G!3baBH"
BADGE_URL = (
    "https://journey.actonacademy.org/schools/805d5c3d/studios/fc376e6b"
    "/progress/users/0d33547e/badge_plan/progress?guide_view=false"
)


def scrape() -> dict:
    s = requests.Session()
    s.headers["User-Agent"] = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )

    # Step 1: GET journey.actonacademy.org → follows redirect to Auth0 login
    print("[1] GET journey.actonacademy.org ...", end=" ", flush=True)
    r1 = s.get("https://journey.actonacademy.org", allow_redirects=True)
    auth0_url = r1.url
    print(f"redirected to: {auth0_url[:80]}...")

    # Parse landing page to get CSRF token for /auth/auth0 POST
    landing_soup = BeautifulSoup(r1.text, "html.parser")

    # Find the /auth/auth0 form and extract its authenticity_token
    auth0_form = landing_soup.find("form", {"action": "/auth/auth0"})
    if not auth0_form:
        # If no login form, we might already be authenticated
        if "badge-assignment-grid-card" in r1.text or "dashboard" in r1.url:
            print("[i] Already authenticated")
            r2 = r1
        else:
            print("[!] Could not find /auth/auth0 form on landing page")
            print(f"[!] Page URL: {r1.url}")
            sys.exit(1)
    else:
        csrf_token = auth0_form.find("input", {"name": "authenticity_token"})["value"]

        # Step 2a: POST to /auth/auth0 to initiate Auth0 redirect
        print("[2a] POST /auth/auth0 to initiate Auth0 flow ...", end=" ", flush=True)
        r_auth0_init = s.post(
            "https://journey.actonacademy.org/auth/auth0",
            data={"authenticity_token": csrf_token, "commit": "Log In"},
            allow_redirects=True,
        )
        auth0_url = r_auth0_init.url
        print(f"landed on: {auth0_url[:80]}")

        if "auth0.com" not in auth0_url:
            print(f"[!] Expected Auth0 redirect, got: {auth0_url}")
            sys.exit(1)

        # Step 2b: POST credentials to Auth0 Universal Login
        print("[2b] POST credentials to Auth0 ...", end=" ", flush=True)
        auth0_soup = BeautifulSoup(r_auth0_init.text, "html.parser")

        hidden_fields = {}
        for inp in auth0_soup.find_all("input", {"type": "hidden"}):
            name = inp.get("name")
            value = inp.get("value", "")
            if name:
                hidden_fields[name] = value

        post_data = {
            **hidden_fields,
            "username": EMAIL,
            "password": PASSWORD,
            "action": "default",
        }

        r2 = s.post(auth0_url, data=post_data, allow_redirects=True)
        print(f"landed on: {r2.url[:80]}")

        if "auth0.com" in r2.url:
            print(f"[!] Still on Auth0 after login — credentials rejected or extra step needed")
            print(f"[!] Status: {r2.status_code}")
            print(f"[!] Snippet:\n{r2.text[:1500]}")
            sys.exit(1)

    # Step 3: GET badge progress page
    print("[3] GET badge progress page ...", end=" ", flush=True)
    s.headers["Accept"] = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
    s.headers["Referer"] = "https://journey.actonacademy.org/"
    r3 = s.get(BADGE_URL, allow_redirects=True)
    print(f"status: {r3.status_code}, final URL: {r3.url[:80]}, size: {len(r3.text):,} chars")

    if "badge-assignment-grid-card" not in r3.text:
        print("[!] Badge page did not load correctly — possibly redirected to login")
        print(f"[!] Page title snippet: {r3.text[:500]}")
        # Try to detect if it's a login redirect
        if "Log In" in r3.text or "sign_in" in r3.url or "auth0" in r3.url:
            print("[!] Detected login redirect — session did not persist")
        sys.exit(1)

    # Step 4: Parse badges
    print("[4] Parsing badges ...")
    soup = BeautifulSoup(r3.text, "html.parser")
    cards = soup.select(".badge-assignment-grid-card")
    print(f"    Found {len(cards)} badge cards total")

    result = {"approved": [], "in_progress": [], "not_started": []}

    for card in cards:
        classes = card.get("class", [])
        data_content = html.unescape(card.get("data-content", ""))
        inner = BeautifulSoup(data_content, "html.parser")
        h6 = inner.select_one("h6.black") or inner.select_one("h6")
        name_raw = h6.text.strip() if h6 else "unknown"
        name = re.sub(r"^L\d+\s*-\s*", "", name_raw).strip()

        if "approved" in classes:
            result["approved"].append(name)
        elif "in_progress" in classes:
            pct_el = card.select_one(".progress-percent")
            result["in_progress"].append({
                "name": name,
                "pct": pct_el.text.strip() if pct_el else "?",
            })
        elif "not_yet_started" in classes:
            result["not_started"].append(name)

    return result


if __name__ == "__main__":
    print("=" * 60)
    print("Mapache Badge Scraper — POC")
    print("=" * 60)

    data = scrape()

    print("\n" + "=" * 60)
    print("RESULTS:")
    print("=" * 60)
    print(json.dumps(data, indent=2, ensure_ascii=False))

    print(f"\nSummary:")
    print(f"  ✅ Completados:   {len(data['approved'])}")
    print(f"  🔄 En progreso:   {len(data['in_progress'])}")
    print(f"  ⬜ No iniciados:  {len(data['not_started'])}")

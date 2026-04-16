"""Aeroplan award scan — full 36-combo grid with auto-login + OTP retry.

Usage:
    python aeroplan_scan.py

Requires:
    - playwright (sync API, uses system Chrome)
    - gmail_otp.py from jal-flights-tracker repo for OTP retrieval
    - .env with AP_USERNAME, AP_PASSWORD
    - secrets/sa.json for Google Sheets
"""

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).parent
ENV_PATH = PROJECT_ROOT / ".env"
RESULTS_PATH = PROJECT_ROOT / "session_results.json"
GMAIL_OTP_DIR = Path(r"C:\dev\jal-flights-tracker")

ORIGINS = ["IAD", "DCA", "BWI"]
DESTS = ["CDG", "FRA", "ZRH"]
DATES = ["2026-09-15", "2026-09-16", "2026-09-17", "2026-09-18"]

BASE_URL = (
    "https://www.aircanada.com/aeroplan/redeem/availability/outbound"
    "?org0={org}&dest0={dest}&departureDate0={date}"
    "&ADT=1&YTH=0&CHD=0&INF=0&INS=0&lang=en-CA&tripType=O&marketCode=INT"
)


def load_env():
    env = {}
    for line in ENV_PATH.read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    return env


# ---------------------------------------------------------------------------
# Playwright helpers
# ---------------------------------------------------------------------------

def is_logged_in(page) -> bool:
    try:
        text = page.evaluate("() => document.body.innerText.slice(0, 400)")
        return bool(re.search(r"Justin|0 pts|My Aeroplan", text, re.I))
    except Exception:
        return False


def has_results(page) -> bool:
    try:
        text = page.evaluate("() => document.body.innerText")
        return bool(re.search(r"\d+\s+flights?\s+found", text, re.I))
    except Exception:
        return False


def do_login(page, username: str, password: str) -> bool:
    """Full login flow: credentials → 2FA email → OTP → submit."""
    print("  Logging in...")
    page.goto("https://www.aircanada.com/", wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(8000)

    # Accept cookies if present
    try:
        accept_btn = page.get_by_role("button", name="Accept all")
        if accept_btn.is_visible(timeout=2000):
            accept_btn.click()
            page.wait_for_timeout(1000)
    except Exception:
        pass

    # Click Sign in — try multiple approaches
    try:
        sign_in = page.get_by_role("button", name="Sign in")
        if sign_in.is_visible(timeout=3000):
            sign_in.click()
        else:
            raise Exception("not visible")
    except Exception:
        page.evaluate("""() => {
            const el = Array.from(document.querySelectorAll('a, button'))
                .find(e => /^sign in$/i.test((e.textContent||'').trim()));
            if (el) el.click();
        }""")

    try:
        page.wait_for_url(re.compile(r"clogin|login"), timeout=20000)
    except Exception:
        page.screenshot(path=str(PROJECT_ROOT / "failures" / "login-debug.png"))
        print(f"  Warning: login page URL not detected (url={page.url}), see failures/login-debug.png")
    page.wait_for_timeout(5000)

    # Type credentials — target the instance inputs (not templates)
    email_loc = page.locator('input[data-gigya-name="loginID"][data-screenset-roles="instance"]')
    email_loc.click(force=True)
    page.wait_for_timeout(300)
    email_loc.type(username, delay=30)
    page.wait_for_timeout(300)

    pass_loc = page.locator('input[data-gigya-name="password"][data-screenset-roles="instance"]')
    pass_loc.click(force=True)
    page.wait_for_timeout(300)
    pass_loc.type(password, delay=30)
    page.wait_for_timeout(300)

    # Submit form by pressing Enter while focus is in the password field
    page.keyboard.press("Enter")
    page.wait_for_timeout(12000)

    # Screenshot 2FA page for debugging
    page.screenshot(path=str(PROJECT_ROOT / "failures" / "2fa-screen.png"))
    print(f"  2FA screen captured (url={page.url})")

    # Click "Send Code" for email
    clicked = page.evaluate("""() => {
        const btns = Array.from(document.querySelectorAll('button'))
            .filter(b => /^send code$/i.test((b.textContent||'').trim()));
        const emailBtn = btns.find(b => (b.parentElement.textContent||'').includes('@gmail.com'));
        if (emailBtn) { emailBtn.click(); return 'clicked email send code'; }
        return 'no email send code button found: ' + btns.map(b => (b.parentElement.textContent||'').trim().slice(0,40)).join(' | ');
    }""")
    print(f"  Send Code result: {clicked}")
    page.wait_for_timeout(3000)

    # Poll for OTP via gmail_otp.py
    print("  Polling for OTP...")
    try:
        result = subprocess.run(
            [sys.executable, "gmail_otp.py", "--poll", "--sender", "aeroplan", "--timeout", "90"],
            cwd=str(GMAIL_OTP_DIR),
            capture_output=True, text=True, timeout=100,
        )
        stdout = result.stdout.strip()
        stderr = result.stderr.strip()
        if result.returncode != 0:
            print(f"  OTP poll error (rc={result.returncode}): {stderr[:200]}")
            print(f"  stdout: {stdout[:200]}")
            return False
        if not stdout:
            print(f"  OTP returned empty. stderr: {stderr[:200]}")
            return False
        parsed = json.loads(stdout)
        otp_code = parsed["code"]
        print(f"  OTP received: {otp_code}")
    except Exception as e:
        print(f"  OTP failed: {e}")
        return False

    # Enter code (page.fill is critical for Gigya)
    page.locator('input[name="emailCode_0"]').fill(otp_code)
    page.wait_for_timeout(500)

    # Click Submit at coordinates (21 hidden submit buttons)
    box = page.locator('input[name="emailCode_0"]').bounding_box()
    if box:
        page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] + 80)

    page.wait_for_timeout(12000)

    logged_in = is_logged_in(page)
    print(f"  Login {'succeeded' if logged_in else 'FAILED'}")
    return logged_in


def extract_business_flights(page, org: str, dest: str, date: str, url: str) -> list[dict]:
    """Extract Business-class flights from the current Aeroplan results page."""
    return page.evaluate("""({ org, dest, date, url }) => {
        const body = document.body.innerText;
        const totalMatch = body.match(/(\\d+)\\s+flights?\\s+found/i);
        if (!totalMatch) return [];

        const sections = body.split(/\\d+ of \\d+/);
        const results = [];

        for (let i = 1; i < sections.length; i++) {
            const s = sections[i];
            const depMatch = s.match(/(\\d{1,2}:\\d{2})\\s*\\n(Non-stop|1 stop)/);
            if (!depMatch) continue;

            const stops = depMatch[2] === 'Non-stop' ? 0 : 1;
            if (stops > 1) continue;

            const durMatch = s.match(/(\\d+hr\\d*m?)/);
            const arrMatch = s.match(/\\+\\d\\s*\\n(\\d{1,2}:\\d{2})/);
            const opMatch = s.match(/(?:Operated by|Includes travel operated by)\\s+([^\\n]+)/);

            const fareList = [];
            const farePattern = /([\\d.]+K)\\s*\\n\\s*\\+\\s*CA\\s*\\$(\\d+)|—/g;
            let m;
            while ((m = farePattern.exec(s)) !== null) {
                if (m[0] === '—') fareList.push(null);
                else fareList.push({ pts: m[1], fee: 'CA$' + m[2] });
            }

            const bizFare = fareList[2] || null;
            if (!bizFare) continue;

            const ptsNum = Math.round(parseFloat(bizFare.pts.replace('K', '')) * 1000);
            const stopMatch = s.match(/\\n([A-Z]{3})\\s*(?:-\\s*[A-Z]{3})?\\s*\\n\\+\\s*\\d+h/);
            const stopAirport = stops === 1 && stopMatch ? stopMatch[1] : '';
            const seatsMatch = s.match(/(\\d+)\\s+seats?\\s+left/i);

            const dep = depMatch[1];
            const arr = arrMatch ? arrMatch[1] : '';
            const dur = durMatch ? durMatch[1] : '';
            const operator = opMatch ? opMatch[1].trim()
                .replace(/Includes travel operated by /i, '')
                .replace(/Operated by /i, '') : '';

            const depClean = dep.replace(/:/g, '');
            const durClean = dur.replace(/\\s/g, '');
            const fp = stops === 0
                ? org + '|' + dest + '|' + date + '|' + depClean + '-' + durClean + '-nonstop'
                : org + '|' + dest + '|' + date + '|' + depClean + '-' + durClean + '-' + stopAirport;

            results.push({
                Fingerprint: fp,
                Origin: org,
                Destination: dest,
                'Depart Date': date,
                'Airline(s)': operator,
                Stops: stops,
                'Stop Airports': stopAirport,
                'Flight Numbers': '',
                'Dep Time': dep,
                'Arr Time': arr ? arr + '+1' : '',
                Duration: dur.replace('hr', 'H, ').replace('m', 'M'),
                Cabin: 'Business',
                Points: ptsNum,
                Fees: bizFare.fee,
                'Seats Left': seatsMatch ? seatsMatch[1] : '',
                'Search URL': url
            });
        }
        return results;
    }""", {"org": org, "dest": dest, "date": date, "url": url})


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    from playwright.sync_api import sync_playwright

    env = load_env()
    ap_user = env["AP_USERNAME"]
    ap_pass = env["AP_PASSWORD"]
    threshold = int(env.get("ALERT_THRESHOLD_POINTS", "150000"))

    all_results = []
    failures = []
    login_count = 0

    combos = [(org, dest, date) for org in ORIGINS for dest in DESTS for date in DATES]
    print(f"Aeroplan scan: {len(combos)} combos")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, channel="chrome")
        context = browser.new_context(
            viewport={"width": 1280, "height": 900},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        )
        page = context.new_page()

        # Initial login
        if not do_login(page, ap_user, ap_pass):
            print("FATAL: initial login failed")
            browser.close()
            return
        login_count += 1

        for i, (org, dest, date) in enumerate(combos, 1):
            url = BASE_URL.format(org=org, dest=dest, date=date)
            print(f"[{i}/{len(combos)}] {org}→{dest} {date} ...", end=" ", flush=True)

            try:
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
                try:
                    page.wait_for_selector("text=flights found", timeout=20000)
                except Exception:
                    page.wait_for_timeout(10000)

                if not has_results(page):
                    # Session expired — re-login
                    print("session expired, re-logging in...", end=" ", flush=True)
                    if not do_login(page, ap_user, ap_pass):
                        failures.append({"org": org, "dest": dest, "date": date, "error": "re-login failed"})
                        print("FAILED")
                        continue
                    login_count += 1

                    # Retry this combo
                    page.goto(url, wait_until="domcontentloaded", timeout=30000)
                    try:
                        page.wait_for_selector("text=flights found", timeout=20000)
                    except Exception:
                        page.wait_for_timeout(10000)

                    if not has_results(page):
                        failures.append({"org": org, "dest": dest, "date": date, "error": "no results after re-login"})
                        print("no results")
                        continue

                page.wait_for_timeout(2000)
                flights = extract_business_flights(page, org, dest, date, url)
                all_results.extend(flights)
                print(f"{len(flights)} biz flights")

            except Exception as e:
                failures.append({"org": org, "dest": dest, "date": date, "error": str(e)[:100]})
                print(f"ERROR: {e!s:.60}")

        try:
            browser.close()
        except Exception:
            pass

    # Save results
    RESULTS_PATH.write_text(json.dumps(all_results, indent=2))
    print(f"\nDone: {len(all_results)} flights, {login_count} logins, {len(failures)} failures")

    if failures:
        print("Failures:")
        for f in failures:
            print(f"  {f['org']}→{f['dest']} {f['date']}: {f['error']}")

    # Write to sheet
    from sheet_client import SheetClient
    client = SheetClient()
    print("Snapshot:", client.upsert_snapshot_bulk(all_results))
    print("History:", client.append_history_bulk(all_results))
    alerts = [{**c, "Threshold Hit": f"Under {threshold // 1000}k"}
              for c in all_results if (c.get("Points") or 0) <= threshold]
    print("Alerts:", client.append_alerts(alerts))
    print(f"Alert count: {len(alerts)}")

    # Cleanup
    RESULTS_PATH.unlink(missing_ok=True)


if __name__ == "__main__":
    main()

"""Aeroplan award search via Patchright (stealth Playwright fork).

Patchright avoids the Runtime.Enable CDP leak that Kasada uses to detect
standard Playwright, allowing the air-bounds API to return real results.

Usage:
    python aeroplan_patchright.py          # full 36-combo scan
    python aeroplan_patchright.py --test   # single combo test (IAD->FRA Sept 16)
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import sys
import time

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
PROFILE_DIR = os.path.join(PROJECT_ROOT, ".patchright-profile")
ENV_PATH = os.path.join(PROJECT_ROOT, ".env")
RESULTS_PATH = os.path.join(PROJECT_ROOT, "session_results.json")

ORIGINS = ["IAD", "DCA", "BWI"]
DESTINATIONS = ["CDG", "FRA", "ZRH"]
DATES = ["2026-09-15", "2026-09-16", "2026-09-17", "2026-09-18"]


def load_env() -> dict:
    env = {}
    with open(ENV_PATH) as f:
        for line in f:
            if "=" in line:
                k, v = line.strip().split("=", 1)
                env[k] = v
    return env


def build_url(org: str, dest: str, date: str) -> str:
    return (
        f"https://www.aircanada.com/aeroplan/redeem/availability/outbound"
        f"?org0={org}&dest0={dest}&departureDate0={date}"
        f"&ADT=1&YTH=0&CHD=0&INF=0&INS=0&lang=en-CA&tripType=O&marketCode=INT"
    )


async def do_login(page, ap_user: str, ap_pass: str) -> bool:
    """Full Aeroplan login flow: credentials + email OTP."""
    await page.goto("https://www.aircanada.com/", wait_until="domcontentloaded")
    await page.wait_for_timeout(5000)

    # Dismiss locale/cookie dialogs
    try:
        btn = page.locator(
            'button:has-text("Continue"), button:has-text("Accept"), '
            'button[aria-label="Close"]'
        ).first
        if await btn.is_visible(timeout=2000):
            await btn.click()
            await page.wait_for_timeout(1000)
    except Exception:
        pass

    # Click Sign in
    try:
        sign_in = page.locator("#libraUserMenu-signIn")
        if await sign_in.is_visible(timeout=3000):
            await sign_in.click()
        else:
            raise Exception("not visible")
    except Exception:
        await page.evaluate("""() => {
            const el = Array.from(document.querySelectorAll('a, button'))
                .find(e => /sign in/i.test((e.textContent||'').trim()));
            if (el) el.click();
        }""")

    try:
        await page.wait_for_url("**/clogin/**", timeout=15000)
    except Exception:
        await page.wait_for_timeout(3000)
    await page.wait_for_timeout(3000)

    # Type credentials (pressSequentially required for Gigya)
    email_field = page.get_by_role("textbox", name="Aeroplan number or email")
    await email_field.press_sequentially(ap_user, delay=30)
    await page.wait_for_timeout(300)

    pass_field = page.get_by_role("textbox", name="Password")
    await pass_field.press_sequentially(ap_pass, delay=30)
    await page.wait_for_timeout(300)

    await page.get_by_role("button", name="Sign in").click()
    await page.wait_for_timeout(8000)

    # 2FA — click Send Code for email
    await page.evaluate("""() => {
        const btns = Array.from(document.querySelectorAll('button'))
            .filter(b => /^send code$/i.test((b.textContent||'').trim()));
        const emailBtn = btns.find(b =>
            (b.parentElement.textContent||'').includes('@gmail.com'));
        if (emailBtn) emailBtn.click();
    }""")
    await page.wait_for_timeout(3000)

    # Poll for OTP via gmail_otp.py
    try:
        result = subprocess.run(
            ["python", "gmail_otp.py", "--poll", "--sender", "aeroplan", "--timeout", "90"],
            cwd=r"C:\dev\jal-flights-tracker",
            capture_output=True, text=True, timeout=100,
        )
        otp_data = json.loads(result.stdout.strip())
        otp_code = otp_data["code"]
    except Exception as e:
        print(f"  OTP poll failed: {e}")
        return False

    # Enter code via page.fill (critical for Gigya)
    await page.locator('input[name="emailCode_0"]').fill(otp_code)
    await page.wait_for_timeout(500)

    # Click Submit via coordinates (Gigya has 21 hidden submit buttons)
    code_box = await page.locator('input[name="emailCode_0"]').bounding_box()
    if code_box:
        await page.mouse.click(
            code_box["x"] + code_box["width"] / 2,
            code_box["y"] + code_box["height"] + 80,
        )

    await page.wait_for_timeout(15000)

    body = await page.evaluate("() => document.body.innerText.slice(0, 500)")
    return bool(re.search(r"Justin|0 pts|My Aeroplan", body, re.I))


async def is_logged_in(page) -> bool:
    url = page.url
    if re.search(r"clogin|login", url, re.I):
        return False
    body = await page.evaluate("() => document.body.innerText.slice(0, 300)")
    return bool(re.search(r"Justin|0 pts|My Aeroplan", body, re.I))


async def detect_page_state(page) -> str:
    """Returns: results | no_flights | invalid_route | login_redirect | unknown"""
    body = await page.evaluate("() => document.body.innerText")
    if re.search(r"flights?\s+found", body, re.I):
        return "results"
    if re.search(r"no flights available", body, re.I):
        return "no_flights"
    if re.search(
        r"we couldn.t find|unable to find|invalid|not.+available.+route|please.+enter.+valid",
        body, re.I,
    ):
        return "invalid_route"
    if re.search(r"clogin|login", page.url, re.I):
        return "login_redirect"
    return "unknown"


async def extract_flights(page, org: str, dest: str, date: str, url: str) -> list[dict]:
    """Extract Business-class flights from the current results page."""
    return await page.evaluate(
        """({ org, dest, date, url }) => {
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
        const durMatch = s.match(/(\\d+hr\\d*m?)/);
        const arrMatch = s.match(/\\+\\d\\s*\\n(\\d{1,2}:\\d{2})/);
        const opMatch = s.match(/(?:Operated by|Includes travel operated by)\\s+([^\\n]+)/);

        const fareList = [];
        const farePattern = /([\\d.]+K)\\s*\\n\\s*\\+\\s*CA\\s*\\$(\\d+)|\\u2014/g;
        let m;
        while ((m = farePattern.exec(s)) !== null) {
          if (m[0] === '\\u2014') fareList.push(null);
          else fareList.push({ pts: m[1], fee: 'CA$' + m[2] });
        }

        const bizFare = fareList[2] || null;
        if (!bizFare) continue;
        if (stops > 1) continue;

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
          ? `${org}|${dest}|${date}|${depClean}-${durClean}-nonstop`
          : `${org}|${dest}|${date}|${depClean}-${durClean}-${stopAirport}`;

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
    }""",
        {"org": org, "dest": dest, "date": date, "url": url},
    )


async def run_scan(test_mode: bool = False):
    from patchright.async_api import async_playwright

    env = load_env()
    ap_user = env.get("AP_USERNAME", "")
    ap_pass = env.get("AP_PASSWORD", "")
    threshold = int(env.get("ALERT_THRESHOLD_POINTS", "150000"))

    if not ap_user or not ap_pass:
        print("ERROR: AP_USERNAME or AP_PASSWORD not set in .env")
        return

    origins = ["IAD"] if test_mode else ORIGINS
    destinations = ["FRA"] if test_mode else DESTINATIONS
    dates = ["2026-09-16"] if test_mode else DATES

    all_results: list[dict] = []
    no_flights: list[dict] = []
    skipped: list[dict] = []
    failures: list[dict] = []
    login_count = 0
    combos_done = 0
    total_combos = len(origins) * len(destinations) * len(dates)

    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            user_data_dir=PROFILE_DIR,
            channel="chrome",
            headless=False,
            no_viewport=True,
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = context.pages[0] if context.pages else await context.new_page()

        # Check login
        await page.goto("https://www.aircanada.com/", wait_until="domcontentloaded")
        await page.wait_for_timeout(5000)

        if not await is_logged_in(page):
            print("Logging in...")
            ok = await do_login(page, ap_user, ap_pass)
            if not ok:
                print("Login failed! Aborting.")
                await context.close()
                return
            login_count += 1
            print("Login successful.")
        else:
            print("Already logged in.")

        # Scan loop
        for org in origins:
            for dest in destinations:
                for date in dates:
                    combos_done += 1
                    url = build_url(org, dest, date)
                    label = f"[{combos_done}/{total_combos}] {org}->{dest} {date}"

                    try:
                        await page.goto(url, wait_until="domcontentloaded", timeout=30000)

                        try:
                            await page.wait_for_selector(
                                "text=/flights? found|no flights available/i",
                                timeout=25000,
                            )
                        except Exception:
                            await page.wait_for_timeout(8000)

                        state = await detect_page_state(page)

                        # Re-login if needed
                        if state in ("login_redirect", "unknown"):
                            print(f"  {label}: session expired, re-logging in...")
                            ok = await do_login(page, ap_user, ap_pass)
                            if not ok:
                                failures.append({"org": org, "dest": dest, "date": date,
                                                 "error": "Re-login failed"})
                                print(f"  {label}: FAIL (re-login failed)")
                                continue
                            login_count += 1

                            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                            try:
                                await page.wait_for_selector(
                                    "text=/flights? found|no flights available/i",
                                    timeout=25000,
                                )
                            except Exception:
                                await page.wait_for_timeout(10000)
                            state = await detect_page_state(page)

                        if state == "no_flights":
                            no_flights.append({"org": org, "dest": dest, "date": date})
                            print(f"  {label}: no flights")
                            continue

                        if state == "invalid_route":
                            skipped.append({"org": org, "dest": dest, "date": date,
                                            "reason": "invalid route or airport"})
                            print(f"  {label}: SKIP (invalid route)")
                            continue

                        if state != "results":
                            failures.append({"org": org, "dest": dest, "date": date,
                                             "error": f"unknown state: {state}"})
                            print(f"  {label}: FAIL ({state})")
                            continue

                        await page.wait_for_timeout(2000)
                        flights = await extract_flights(page, org, dest, date, url)
                        biz_count = len(flights)
                        all_results.extend(flights)
                        print(f"  {label}: {biz_count} Business flights")

                    except Exception as e:
                        err_msg = str(e)[:100]
                        failures.append({"org": org, "dest": dest, "date": date,
                                         "error": err_msg})
                        print(f"  {label}: FAIL ({err_msg})")

        await context.close()

    # Write results
    with open(RESULTS_PATH, "w") as f:
        json.dump(all_results, f, indent=2)

    # Write to sheet
    if all_results:
        print(f"\nWriting {len(all_results)} results to sheet...")
        try:
            write_code = f"""
import json, os, sys
sys.path.insert(0, {PROJECT_ROOT!r})
from sheet_client import SheetClient
with open({RESULTS_PATH!r}) as f:
    cells = json.load(f)
threshold = {threshold}
client = SheetClient()
print('Snapshot:', client.upsert_snapshot_bulk(cells))
print('History:', client.append_history_bulk(cells))
alerts = [{{**c, 'Threshold Hit': f'Under {{threshold//1000}}k'}} for c in cells if (c.get('Points') or 0) <= threshold]
print('Alerts:', client.append_alerts(alerts))
print(f'Alert count: {{len(alerts)}}')
"""
            result = subprocess.run(
                ["python", "-c", write_code],
                capture_output=True, text=True, timeout=60,
            )
            print(result.stdout)
            if result.stderr:
                print(f"Sheet errors: {result.stderr[:200]}")
        except Exception as e:
            print(f"Sheet write failed: {e}")

    # Clean up
    if os.path.exists(RESULTS_PATH):
        os.remove(RESULTS_PATH)

    # Summary
    print(f"\n{'='*50}")
    print(f"AEROPLAN SCAN SUMMARY")
    print(f"{'='*50}")
    print(f"Combos searched: {combos_done}/{total_combos}")
    print(f"Results with Business flights: {combos_done - len(no_flights) - len(skipped) - len(failures)}")
    print(f"No flights available: {len(no_flights)}")
    print(f"Skipped (invalid route): {len(skipped)}")
    print(f"Failures: {len(failures)}")
    print(f"Total Business flights captured: {len(all_results)}")
    print(f"Login count: {login_count}")

    if all_results:
        min_pts = min(r["Points"] for r in all_results)
        min_flights = [r for r in all_results if r["Points"] == min_pts]
        print(f"Min points: {min_pts:,} ({min_flights[0]['Origin']}->{min_flights[0]['Destination']} {min_flights[0]['Depart Date']})")
        alerts = [r for r in all_results if r["Points"] <= threshold]
        print(f"Alerts (under {threshold//1000}k): {len(alerts)}")

    if no_flights:
        print(f"\nNo-flights combos:")
        for nf in no_flights:
            print(f"  {nf['org']}->{nf['dest']} {nf['date']}")

    if skipped:
        print(f"\nSkipped combos:")
        for sk in skipped:
            print(f"  {sk['org']}->{sk['dest']} {sk['date']}: {sk['reason']}")

    if failures:
        print(f"\nFailed combos:")
        for fl in failures:
            print(f"  {fl['org']}->{fl['dest']} {fl['date']}: {fl['error']}")


if __name__ == "__main__":
    test_mode = "--test" in sys.argv
    asyncio.run(run_scan(test_mode=test_mode))

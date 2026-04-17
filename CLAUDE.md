# oktoberfest-flight-search

Automated scraper that runs on a schedule to find **pure-business one-way award tickets** from the Washington DC area to Europe for an Oktoberfest trip. Drives multiple airline sites via the Playwright MCP using logged-in loyalty-program sessions, filters out mixed-cabin itineraries, and writes results to a Google Sheet.

## Mission

Catch pure-business-class award availability for one-way WAS → CDG / FRA / ZRH on September 15, 16, 17, or 18, 2026, using any AMEX Membership Rewards transfer partner. Capture data on every run so we can see trends, and alert (via Sheet append) on any pure-business itinerary at or below 150k points (see `ALERT_THRESHOLD_POINTS`).

**Phase 1 (active):** United MileagePlus — covers United metal + Star Alliance partners (LH, LX, OS, SK, TK, etc.) bookable with UA miles.

**Phase 2 (pending Flying Blue credentials):** Air France / KLM via Flying Blue — covers AF/KL metal + SkyTeam partners, critically the CDG nonstops that UA can't reach. See the Air France section below.

## Constants (do not change without asking)

- **Origins:** `BWI`, `DCA`, `IAD`
- **Destinations:** `CDG`, `FRA`, `ZRH`
- **Dates:** `2026-09-15`, `2026-09-16`, `2026-09-17`, `2026-09-18`
- **Cabin (v1):** Business only
- **Max stops:** 1
- **Mixed-cabin policy:** Aeroplan shows a "X% in Business Class" indicator on fare cells via a hidden `.mixed-cabin-percentage` div. Flights with ≥85% in Business pass; below 85% are rejected. 100% Business flights have no such div. United uses a "Mixed cabin" text marker instead — reject any card showing it within the Business fare section.
- **Alert threshold:** `ALERT_THRESHOLD_POINTS` from `.env` (default `150000`)
- **Passengers:** 1 adult
- **Trip type:** one-way

Per airline, a full run = 3 origins × 3 destinations × 4 dates = **36 searches**.

## Architecture

Same pattern as sibling projects `jal-flights-tracker` and `pc-deal-tracker`:

- **Playwright MCP** — real Chrome with a persistent browser profile managed by MCP (NOT the `.playwright-mcp/` folder in this repo — that's only runtime logs/snapshots, safe to delete). Profile state is where auth/2FA cookies live and persists across `browser_close`.
- **`aeroplan-extension/`** — Chrome extension for Aeroplan scans. User loads it unpacked, clicks "Start Scan", it navigates 36 combos in a tab and downloads a JSON file. Bypasses Kasada by running in the user's real browser.
- **`ingest_scan.py`** — Ingests a scan JSON (from the extension or `scans/`), looks up cash prices, writes to Google Sheet. Auto-finds the latest scan if no path given. Copies files to `scans/` for historical record.
- **`sheet_client.py`** — Google Sheets client with `upsert_snapshot_bulk`, `append_history_bulk`, `append_alerts`
- **`update_sheet.py`** — CLI wrapper for `init` / debugging
- **`.env`** — `UA_USERNAME`, `UA_PASSWORD`, `FB_USERNAME`, `FB_PASSWORD`, `ALERT_THRESHOLD_POINTS`
- **`secrets/sa.json`** — Google service account key (same SA as the sibling trackers)

## ⚠️ Login & 2FA (critical — read this first)

United's **pure award search** (`at=1` param) requires MileagePlus login. Session cookies do NOT persist across `browser_close`, so **every run must log in**. What DOES persist (in the Playwright MCP browser profile) is the "Remember this browser" cookie — so as long as that cookie is valid, re-login doesn't trigger SMS 2FA. If the SMS prompt DOES fire, the cookie has been invalidated and a human must complete it once interactively with the checkbox ticked. This cookie lives inside Playwright MCP's internal profile, not in `.playwright-mcp/` in this repo, so routine cleanup of repo files does not wipe it.

Login flow (each run):
1. Navigate to `https://www.united.com/en/us/`, dismiss cookie banner if present.
2. Click the "Sign in" button (outer). `MPIDEmailField` textbox appears in a dialog — set value = `UA_USERNAME`, dispatch input+change, click Continue.
3. `password` field appears. Set value = `UA_PASSWORD` (never log), click the "Sign in" button inside the dialog.
4. Wait ~5s. If body text contains `Hi, Justin` + miles balance, success. If a "verification code sent to ******NNNN" appears, the Remember-this-browser cookie has lapsed — abort with a failure artifact and surface to the operator.

Do **not** hard-code a way around SMS 2FA. That's a security boundary; we lean on the persistent browser profile instead.

## How to Run a Session

Create a task at the start with `TaskCreate`. Mark it in_progress. Mark completed at the end.

### Step 1 — Prepare

Read `.env`. Pull `ALERT_THRESHOLD_POINTS` (default `150000`). **Never print `UA_PASSWORD` to stdout.**

Build the search grid — 36 combos of (origin, destination, date). Initialize an empty results list.

If the Google Sheet's tabs don't exist yet, run once: `python update_sheet.py init`.

### Step 2 — Open United and verify login

```
mcp__playwright__browser_navigate → https://www.united.com/en/us/
```

Dismiss the cookie banner if present (button text "Accept cookies"). Then confirm logged-in state:

```js
() => /Hi,\s*Justin/i.test(document.body.innerText)
```

If not logged in, abort this run and save a failure artifact — a human needs to log in manually + SMS + check "Remember this browser".

### Step 3 — Loop over the 36 combos

For each (origin, destination, date) combo, use the **form-submit** path. Direct award URLs with `at=1` hang indefinitely at "Loading results…" unless submitted through the form first (server-generated `pst` token is required). Per combo:

1. `browser_navigate` to `https://www.united.com/en/us/`.
2. `browser_evaluate` — one async call that does ALL form filling, date selection, and submit. Template (substitute ORIGIN, DEST, DAY):
   ```js
   async () => {
     await new Promise(r => setTimeout(r, 1500));
     const ORIGIN='<IAD|DCA|BWI>', DEST='<CDG|FRA|ZRH>', DAY=<15|16|17|18>;
     const tab = document.getElementById('travelTab');
     if (tab && tab.getAttribute('aria-selected') !== 'true') tab.click();
     await new Promise(r => setTimeout(r, 800));
     document.getElementById('radiofield-item-id-flightType-1').click();
     const award = document.getElementById('award');
     if (!award.checked) award.click();
     await new Promise(r => setTimeout(r, 300));
     const setInp = (el, v) => {
       const s = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
       s.call(el, v);
       el.dispatchEvent(new Event('input', { bubbles: true }));
       el.dispatchEvent(new Event('change', { bubbles: true }));
     };
     setInp(document.getElementById('bookFlightOriginInput'), ORIGIN);
     setInp(document.getElementById('bookFlightDestinationInput'), DEST);
     const cabin = document.getElementById('cabinType');
     const ss = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value').set;
     ss.call(cabin, '2');
     cabin.dispatchEvent(new Event('change', { bubbles: true }));
     await new Promise(r => setTimeout(r, 400));
     document.getElementById('DepartDate_start').click();
     await new Promise(r => setTimeout(r, 900));
     // Day buttons have NO aria-label when a price overlay is present. Match by text.
     // Button text is "{day}{price}k" (e.g. "1640k" = day 16, 40k miles).
     // Picker shows 2 months side-by-side; DOM order places Sep before Oct,
     // so .find() returns the September cell first.
     const target = Array.from(document.querySelectorAll('button.rdp-day_button'))
       .find(b => new RegExp('^' + DAY + '(\\d|$)').test((b.textContent||'').trim()));
     if (!target) return { error: 'no sept day ' + DAY };
     target.click();
     await new Promise(r => setTimeout(r, 400));
     const find = Array.from(document.querySelectorAll('button'))
       .find(b => b.getAttribute('aria-label') === 'Find flights' && b.offsetParent !== null);
     if (!find) return { error: 'no find button' };
     find.click();
     return { submitted: true };
   }
   ```
3. `browser_wait_for` 14s. Results URL contains `&at=1&` and `&pst=<token>&`.
4. `browser_evaluate` — extract structured data (see Step 4). **Do NOT use `browser_snapshot` on the results page** — it's 20+ KB. Use text extraction.

### Step 4 — Extract results per combo

Run this in `browser_evaluate`. Three critical gotchas it handles:
- **Stops regex**: the card text starts with `NONSTOP` *or* `N STOP` / `N STOPS`. The earlier `^(NONSTOP|\d+)\s*STOPS?` pattern requires "STOP" after the captured group, which fails for NONSTOP. Use `^(NONSTOP|(\d+)\s*STOPS?)` and check group 1 for /nonstop/.
- **Mixed-cabin scope**: the inline text "Mixed cabin" frequently appears in the *Premium Economy* block of a 1-stop itinerary that still has pure Business available. Only check for "Mixed cabin" within the Business section (between `Business (lowest)` and `Flight Information`), not the whole card.
- **Flight numbers missing on 1-stop**: United collapses segment detail on 1-stop cards. Flight numbers are only in the DOM if you click "Details" to expand. For the tracker we accept that and fall back to a time+duration+stop fingerprint instead of a flight-number fingerprint.

```js
() => {
  const body = document.body.innerText;
  const cards = [];
  const re = /((?:NONSTOP|\d+\s+STOPS?))[\s\S]*?(?=(?:NONSTOP|\d+\s+STOPS?|Site Feedback|$))/g;
  let m; while ((m = re.exec(body)) !== null) cards.push(m[0]);
  const AM = {UA:'United',LH:'Lufthansa',LX:'Swiss',LO:'LOT',OS:'Austrian',SN:'Brussels',SK:'SAS',TK:'Turkish',TP:'TAP',AC:'Air Canada',NH:'ANA',AV:'Avianca',ET:'Ethiopian',CA:'Air China',EW:'Eurowings'};
  const seen = new Set();
  return cards.map(c => {
    const t = c.trim();
    const sm = t.match(/^(NONSTOP|(\d+)\s*STOPS?)/i);
    const sn = sm ? (/nonstop/i.test(sm[1]) ? 0 : parseInt(sm[2], 10)) : null;
    const dp = (c.match(/(\d{1,2}:\d{2}\s*[AP]M)\s*\nDeparting/i) || [])[1] || '';
    const ar = (c.match(/(\d{1,2}:\d{2}\s*[AP]M)\s*\nArriving/i) || [])[1] || '';
    const nd = /Arrives Sep/i.test(c) ? '+1' : '';
    const dr = (c.match(/(\d+H,?\s*\d*M?)\s*\nDuration/i) || [])[1] || '';
    const fl = [...new Set([...c.matchAll(/\b(UA|LH|LX|LO|OS|SK|SN|TK|TP|AC|NH|AV|ET|CA|EW)\s*(\d{1,4})\b/gi)]
      .map(x => x[1].toUpperCase() + x[2]))];
    const sa = (c.match(/Destination[^\n]*\(([A-Z]{3})\)\s*\n[A-Z]{3}\s*\n/) || [])[1] || '';
    // Mixed-cabin detection limited to the Business section only:
    const busSection = (c.match(/Business\s*\(lowest\)[\s\S]*?(?=\nFlight Information|$)/i) || [''])[0];
    const busMixed = /Mixed cabin/i.test(busSection);
    // True saver price (Cardmember rate will be ~10% less and appears earlier in the section):
    const bm = c.match(/Select fare for Business[\s\S]*?(\d+)k?\s*miles\s*\+\s*(\$[\d.]+)/i);
    const bp = bm ? parseInt(bm[1]) * 1000 : null;
    const bf = bm ? bm[2] : '';
    const al = [...new Set(fl.map(f => AM[f.slice(0,2)] || f.slice(0,2)))].join(', ') || (sn === 0 ? 'United' : 'Unknown');
    return { sn, dp, ar: ar + nd, dr, fl, sa, mx: busMixed, bp, bf, al };
  }).filter(p => {
    if (p.sn === null || p.sn > 1 || !p.bp || p.mx) return false;
    const key = p.fl.length ? [...p.fl].sort().join('+') : (p.dp + '|' + p.dr + '|' + p.sa);
    if (seen.has(key)) return false;
    seen.add(key); return true;
  });
}
```

Build per-flight records with this shape:

```json
{
  "Fingerprint": "IAD|FRA|2026-09-16|UA989",
  "Origin": "IAD",
  "Destination": "FRA",
  "Depart Date": "2026-09-16",
  "Airline(s)": "United",
  "Stops": 0,
  "Stop Airports": "",
  "Flight Numbers": "UA989",
  "Dep Time": "5:25 PM",
  "Arr Time": "7:20 AM+1",
  "Duration": "7H, 55M",
  "Cabin": "Business",
  "Points": 200000,
  "Fees": "$5.60",
  "Seats Left": ""
}
```

**Fingerprint rule:** use flight numbers if present; otherwise fall back to `{Origin}|{Dest}|{Date}|{DepTime}-{Duration}-{StopAirport}` (for 1-stop cards whose flight numbers aren't inline). Strip spaces from DepTime/Duration when building the key.

Airline mapping: UA→United, LH→Lufthansa, LX→Swiss, LO→LOT, OS→Austrian, SN→Brussels, SK→SAS, TK→Turkish, TP→TAP, AC→Air Canada, NH→ANA, AV→Avianca, ET→Ethiopian, CA→Air China, EW→Eurowings. When `fl` is empty and `sn === 0`, assume `United` (metal is obvious for a nonstop WAS-FRA/ZRH). Otherwise `Unknown`.

Between searches, always `browser_navigate` back to `https://www.united.com/en/us/` and re-fill from scratch. Do NOT close the browser between combos — that wipes the session cookie and forces a re-login.

**Performance:** budget ~45s per search. If a search exceeds 60s or returns zero cards, save a failure artifact (see below) and continue to the next combo. Do not abort the whole session.

### Step 5 — Write to the Sheet

Accumulate all results in memory, then write once at the end. Write to `session_results.json` first to avoid Windows command-line length limits:

```
Write session_results.json with the full records array.

Bash: python -c "
import json, os
from sheet_client import SheetClient
with open('session_results.json') as f:
    cells = json.load(f)
threshold = 150000
for line in open('.env'):
    if line.startswith('ALERT_THRESHOLD_POINTS='):
        threshold = int(line.split('=',1)[1].strip())
client = SheetClient()
print('Snapshot:', client.upsert_snapshot_bulk(cells))
print('History:', client.append_history_bulk(cells))
alerts = [{**c, 'Threshold Hit': f'Under {threshold//1000}k'} for c in cells if (c.get('Points') or 0) <= threshold]
print('Alerts:', client.append_alerts(alerts))
print('Alert rows:', json.dumps(alerts, indent=2))
"
```

If a sheet call errors, log it and continue — missed `Snapshot` upserts can be reconstructed from `History`.

### Step 6 — Close the browser and print a summary

```
mcp__playwright__browser_close
```

Print:
- Combos searched: 36
- Combos with at least one pure-business result: X
- Combos with zero results: list `(origin, dest, date)`
- Total flight options captured
- Min points across all results, with route/date/flight numbers
- Count of alerts triggered and their brief details
- Snapshot rows inserted/updated
- History/Alerts rows appended
- Any failure artifacts written

### Step 7 — Delete the scratch file

```
Bash: rm session_results.json
```

## Google Sheet Schema

URL is in `sheet-config.json`. Three tabs:

- **`Snapshot`** (one row per `Fingerprint`, upserted): `Fingerprint`, `Origin`, `Destination`, `Depart Date`, `Airline(s)`, `Stops`, `Stop Airports`, `Flight Numbers`, `Dep Time`, `Arr Time`, `Duration`, `Cabin`, `Points`, `Fees`, `Cash Price (USD)`, `CPP`, `Seats Left`, `Search URL`, `Lowest Points Ever`, `Lowest Points Date Seen`, `First Seen`, `Last Scanned`.
- **`History`** (append-only): same identity + scan time, no rollups.
- **`Alerts`** (append-only): scan time, route/date/flights, `Threshold Hit`, `Emailed`.

`Fingerprint` = `{Origin}|{Dest}|{Date}|{Flight Numbers}` — e.g. `IAD|FRA|2026-09-16|UA989` or `BWI|FRA|2026-09-17|UA928+LH456` for multi-leg.

## Secrets and Config

- `.env` — `UA_USERNAME`, `UA_PASSWORD`, `ALERT_THRESHOLD_POINTS`. Never echo `UA_PASSWORD`.
- `secrets/sa.json` — Google service account key. Sheet is shared with `pc-deal-tracker@thunderhead-projects.iam.gserviceaccount.com`.
- `sheet-config.json` — sheet URL + tab names.
- `.playwright-mcp/` — Playwright persistent profile. **Surviving this across runs is how we skip SMS 2FA.** Never delete.

All of `secrets/`, `.env`, `failures/`, `.playwright-mcp/`, and `tracker-log.txt` are gitignored.

## Failure Handling

When a step fails unexpectedly:
1. `mcp__playwright__browser_take_screenshot` → save under `failures/<timestamp>-<origin>-<dest>-<date>.png`.
2. Save `document.documentElement.outerHTML` via evaluate → `failures/<timestamp>-<origin>-<dest>-<date>.html`.
3. Log the error and the paths in the summary.
4. Do not retry blindly — surface the failure so the next debugging session can learn.

A single failed search does NOT abort the rest of the session. Record it in the summary and move on.

## Validated Findings (from first manual run, 2026-04-15)

**End-to-end confirmed working on 7 combos** covering all 3 origins and 2 destinations:

- **IAD → FRA** (all 4 dates): 2 nonstops daily — UA989 (5:25 PM, 7h55m) and UA932 (10:10 PM, 8h) — both **200k miles** saver (180k cardmember) + $5.60.
- **IAD → ZRH** Sept 15: UA52 nonstop (5:45 PM, 8h20m) at **200k miles** + $5.60.
- **DCA → FRA** Sept 16: 8 pure-business 1-stop options via IAH or EWR, all **200k miles**.
- **BWI → FRA** Sept 16: 11 pure-business 1-stop options via ORD / IAH / SFO / DEN at **200k** or **245k miles**.

**Key learnings from run:**
- The direct award URL (`.../choose-flights?...&at=1&...`) hangs at "Loading results…" if opened without first submitting the form in the same session. Always submit through the home-page form.
- Calendar overlay prices (e.g. "40k" on Sept 15-18) are the **cheapest ANY cabin** for that day — NOT business-specific. Ignore them; always click into the date and read the Business (lowest) row on the results page.
- Day buttons in the picker have NO aria-label when a price overlay is present. Click by text pattern `^{DAY}(\d|$)` on `button.rdp-day_button`. The picker shows 2 months side-by-side; DOM order places the leftmost month first, so `.find()` returns the September cell.
- `browser_close` wipes the session cookie but leaves the "Remember this browser" 2FA cookie intact → next run does a plain username+password login without SMS. If SMS prompt appears, the cookie has rolled and a human needs to re-auth interactively.
- No pure-business options surfaced under **150k miles** at scrape time. Baseline is 200k from WAS→FRA; watch for drops.
- Lufthansa (LH) nonstops to FRA appear on united.com results but show "Not available" on the Business (lowest) row — their saver inventory isn't open via MileagePlus right now. Separate Lufthansa Miles&More search may surface different pricing for the same metal.

## Phase 2: Flying Blue via KLM.com (end-to-end working)

**Goal:** extend the tracker to cover Air France + KLM awards, priced in Flying Blue miles. AMEX Membership Rewards transfers to Flying Blue 1:1, same economics as UA. This closes the **CDG gap** (United has no nonstop WAS→CDG on Star Alliance; AF 55 runs IAD→CDG daily) and often surfaces lower saver pricing than UA on the same corridors (Flying Blue saver business to Europe typically 55–75k pts with monthly promos).

### Status

- ✅ Login validated end-to-end (2026-04-15). Account `5346859161` is active. Header shows `JM` avatar post-login. "Keep me logged in" cookie persists across `browser_close`.
- ✅ **KLM.com end-to-end working (2026-04-15).** Full search + extraction validated for IAD→CDG 2026-09-16 Business. 6 flights captured (2 nonstops, 4 connecting), results written to Google Sheet.
- ⛔ **airfrance.us blocked by Akamai Bot Manager** — GraphQL returns 403. Same `_abck`/`bm_sz` cookies. Use **klm.com** instead — same Flying Blue inventory, same credentials, no bot block. Screenshot: `failures/2026-04-15-af-akamai-block.png`.
- ✅ Date picker works on both AF and KLM using pointer-event recipe (see below). KLM uses "Confirm dates" button text (AF uses "Confirm").
- ✅ Results extraction working — flight cards parsed from body text (split by "Details\n"), flight numbers extracted from bottom-sheet detail panels opened per-card.

### Prerequisites

- `FB_USERNAME` and `FB_PASSWORD` in `.env` — filled in. Same Flying Blue account works on both airfrance.us and klm.com.
- Flying Blue OTP delivery is **email-only** right now ("Due to recent regulatory changes, our SMS service is currently unavailable"). PIN code arrives at the account's email on file (same Gmail we use for JAL). On cookie lapse, a human completes it interactively.
- The jal-flights-tracker repo has `gmail_otp.py` which polls the same Gmail inbox for JAL OTPs. If Flying Blue OTP retrieval needs to be automated later, adapt that script with a Flying Blue sender filter.

### Source

Drive **`https://www.klm.com/`** (NOT airfrance.us — blocked by Akamai). Use the on-page **Book with Miles** toggle switch. FlyingBlue.com itself is a loyalty-marketing site with no search form — award search only lives on airfrance.us / klm.com, gated by Flying Blue login. KLM and AF share the same Flying Blue inventory.

### Form structure (validated on KLM.com 2026-04-15 while logged in)

KLM uses the same AFKL Angular Material form as airfrance.us. Key differences noted below.

- **"Book with Miles" toggle**: `button#mat-mdc-slide-toggle-server-app0-button` — a `mdc-switch` (NOT a tab like on AF). Use pointer-event dispatch. When on: `mdc-switch--selected mdc-switch--checked`.
- **Trip** `mat-select[aria-label="Trip"]` — click, then click `mat-option` matching "One-way".
- **Cabin** `mat-select[aria-label="Select travel class"]` (KLM uses "Select travel class", AF uses "Select a cabin") — click, then click `mat-option` matching "Business".
- **From** `span#bwsfe-station-picker-input-2` — `contenteditable`. Set `textContent`, dispatch `input` + `keyup`. Click matching `mat-option`.
- **To** `span#bwsfe-station-picker-input-3` — same pattern.
- **Departure date** — same `bw-search-datepicker` as AF. See "Date picker — working recipe" below.
- **Passengers**: keep default 1 adult.
- **Search flights** button: `button[data-testid="bwsfe-widget__search-button"]`.

### Date picker — working recipe (validated 2026-04-15)

Plain `.click()` on the datepicker toggle button does NOT open the overlay — Angular Material requires the full pointer-event sequence. The working approach for every stage (toggle, Next-month, day cell, Confirm) is:

```js
const firePointerClick = (el) => {
  const r = el.getBoundingClientRect();
  const o = {bubbles: true, cancelable: true, clientX: r.left + r.width/2, clientY: r.top + r.height/2, button: 0, pointerType: 'mouse', isPrimary: true};
  el.dispatchEvent(new PointerEvent('pointerdown', o));
  el.dispatchEvent(new MouseEvent('mousedown', o));
  el.dispatchEvent(new PointerEvent('pointerup', o));
  el.dispatchEvent(new MouseEvent('mouseup', o));
  el.dispatchEvent(new MouseEvent('click', o));
};
```

Full date-selection flow (for target year/month/day, e.g. September 16 2026):

```js
async () => {
  // 1. Open overlay
  const toggle = document.querySelector('[data-testid="bwsfe-datepicker__toggle-button"]');
  firePointerClick(toggle);
  await new Promise(r => setTimeout(r, 800));

  const pane = document.querySelector('.cdk-overlay-pane.bwc-date-picker-overlay');
  const ariaTarget = '16 September 2026'; // day-month-year, 2-digit day

  // 2. Advance months until the target day is rendered.
  //    The picker appends new months to a virtual-scrolled list (April → April+May → Apr+May+June...),
  //    so you only need to click Next as many times as months separate today from the target.
  const next = pane.querySelector('button[aria-label="Next month"]');
  for (let i = 0; i < 12; i++) {
    if (pane.querySelector(`button[aria-label="${ariaTarget}"]`)) break;
    firePointerClick(next);
    await new Promise(r => setTimeout(r, 400));
  }

  // 3. Click the day cell. It gets class `bwc-day--selected` but overlay stays open.
  const day = pane.querySelector(`button[aria-label="${ariaTarget}"]`);
  day.scrollIntoView({block: 'center'});
  await new Promise(r => setTimeout(r, 200));
  firePointerClick(day);
  await new Promise(r => setTimeout(r, 400));

  // 4. Click Confirm (text-match, inside overlay).
  const confirm = Array.from(pane.querySelectorAll('button')).find(b => /^confirm$/i.test((b.textContent||'').trim()));
  firePointerClick(confirm);
  await new Promise(r => setTimeout(r, 600));
}
```

After this runs, `.bw-search-datepicker__field-wrapper` innerText reads `"Departure date\nSept 16"` and the overlay is gone. The toggle button may keep `aria-expanded="true"` even after close — ignore that and look for overlay disappearance instead.

### Known blocker: Akamai Bot Manager (2026-04-15)

After the form is filled and Search flights is clicked, `/gql/v1?...&operationName=SearchResultAvailableOffersQuery` returns **HTTP 403**. The cookie jar shows Akamai Bot Manager markers (`_abck`, `bm_sz`, `bm_mi`, `bm_sv`). Things that did **not** fix it:

- Clicking Search via MCP `browser_click` (real Playwright click) vs `browser_evaluate` dispatched click — both blocked.
- Reloading `/search/flights/0` — still 403 on the GQL call.
- Navigating from home and re-submitting — still 403.

A direct `fetch('https://wwws.airfrance.us/gql/v1?bookingFlow=LEISURE&operationName=Hello')` from the page context returns **403** with Akamai headers, confirming the block is at the edge, not in the Angular app.

Plausible next steps for the operator (not yet attempted):
- Open airfrance.us in a real Chrome window on the same machine, do a manual search with genuine mouse movement, then let Playwright pick up the warmed `_abck` cookie. Note: Playwright MCP's internal profile is separate from regular Chrome, so cookies would need to be copied across.
- Apply `playwright-extra` stealth patches (override `navigator.webdriver`, plugin list, permissions API, etc.) to the MCP-managed Chrome launch. That requires wrapping/replacing the MCP rather than calling it.
- Route requests through a residential-proxy browser automation service that handles Akamai sensor-data for you.
- Try KLM.com instead of airfrance.us — same Flying Blue account works, and KLM's anti-bot posture may differ.

Once the block is bypassed, the rest of Phase 2 (results parsing, sheet writes) is still unmapped — haven't seen a rendered flight card yet.

Unlike UA, AF has no equivalent of the "Money + Miles" hybrid mode — the Book with Miles tab only shows award pricing and is gated by Flying Blue login.

### Run loop (sketch — flesh out during validation)

1. Skip Phase 2 if `FB_USERNAME` or `FB_PASSWORD` is empty. Write a note to the summary.
2. `browser_navigate` to `https://wwws.airfrance.us/`.
3. Accept cookie banner (`button:has-text("Accept")`).
4. Click the Log in button (header) and complete the Flying Blue login flow. Verify with a post-login element (account menu, miles balance, or profile avatar) before proceeding.
5. Click the **Book with Miles** tab.
6. For each of the 36 `(origin, destination, date)` combos: set trip type = One way, cabin = Business, origin/destination IATA, pick date from the calendar, click Search.
7. Extract results (same parser concepts as United; Flying Blue results likely use a different DOM so selectors will need discovery on the first run). Fields to pull: dep/arr times, duration, stops + stop airports, operating airline(s) (AF, KL, DL, KE, etc.), Business miles price + fees, seats left if shown. Flag mixed-cabin only within the Business fare section.
8. Accumulate into the same `session_results.json` structure as Phase 1. Airline(s) will typically be `Air France`, `KLM`, or a multi-airline chain like `Air France, Delta`.
9. When Phase 1 (UA) and Phase 2 (AF) both complete, run one combined upsert/history/alert write (so the sheet holds everything from the session).

### Airline mapping additions for Phase 2

`AF → Air France`, `KL → KLM`, `DL → Delta`, `KE → Korean Air`, `VS → Virgin Atlantic`, `MU → China Eastern`, `SU → Aeroflot` (hist.), `RO → TAROM`, `CI → China Airlines`, `KQ → Kenya Airways`.

### Validated findings (from first KLM.com run, IAD→CDG 2026-09-16)

**6 flights captured (2 nonstops, 4 connecting), all pure Business:**

- **AF51** — 18:15→08:00+1, nonstop, 7h45, Boeing 777-300, **319,000 Miles** + $349.70
- **AF53** — 21:45→11:15+1, nonstop, 7h30, Airbus A350-900, **183,000 Miles** + $349.70 (**lowest**)
- **KL652+AF1241** — 18:10→10:55+1, 1 stop AMS, 10h45, **364,500 Miles** (2 seats left)
- **KL652+AF1341** — 18:10→12:00+1, 1 stop AMS, 11h50, **364,500 Miles**
- **KL652+KL1407** — 18:10→13:40+1, 1 stop AMS, 13h30, **319,000 Miles**
- **DL5076+AF9** — 19:59→12:30+1, 1 stop JFK (Endeavor Air), 10h31, **700,000 Miles**

**Key learnings:**
- KLM.com bypasses Akamai bot detection that blocks airfrance.us. Same Flying Blue inventory, same credentials.
- Calendar strip shows miles pricing per day: 176k–319k range across Sept 13–19. Cheapest days are Sun/Thu/Sat at 176k.
- Fees are **$349.70** across the board — significantly higher than UA's $5.60.
- Results page uses `[class*="flight-card"]` for card elements; pricing is in sibling elements, not within the card. Parse from body text by splitting on `\nDetails\n`.
- Flight numbers are only visible in the bottom-sheet detail panel (`bwsfc-flight-details-bottom-sheet`) — click each "Details" button, extract `Operated by: XXNNNN | Aircraft`, then close panel before opening the next.
- KLM uses "Confirm dates" (not "Confirm") in the datepicker overlay.
- All connecting flights route via AMS (Schiphol). The IAD→AMS leg is KL652 on all three AMS-connecting options.
- No promos surfaced at scrape time. Lowest business fare is 183k (AF53 nonstop) — well above 150k threshold.

### Things to watch for

- Flying Blue **monthly Promo Rewards** can drop biz to ~40–50k one-way on specific corridors, but they're date-specific.
- CDG→FRA/ZRH rerouting: from CDG, client takes the train, so any routing landing at CDG is fine.

### Split-vs-combined scheduling

Each phase takes ~20 min of real-time scraping. Combined session = ~40 min per scheduled run. If `claude -p` has issues with that duration under Task Scheduler, split into two scheduler tasks (UA at 08:00, AF at 08:30) using different entry prompts in `run-tracker.bat`. Keep combined for now until we see a problem.

## Phase 3: Aeroplan (working via Patchright — bypasses Kasada)

**Goal:** search Aeroplan (Air Canada's Star Alliance program) for Lufthansa nonstops to FRA and Swiss nonstops to ZRH. AMEX MR transfers to Aeroplan 1:1. Aeroplan removed fuel surcharges on LH/LX, making it the best program for booking these flights. AMEX cannot transfer directly to Miles & More (LH/LX's own program).

### Status

- ✅ Credentials in `.env` (`AP_USERNAME`, `AP_PASSWORD`). Account is `michelotti12@gmail.com`.
- ✅ **Login end-to-end working (2026-04-15).** Header shows "Justin" and "0 pts" post-login. "Book with Aeroplan points" toggle works — no "Please sign in" prompt. See login recipe below.
- ✅ 2FA works via email OTP — `gmail_otp.py --poll --sender "aeroplan"` from `jal-flights-tracker` repo. Sender is `info@communications.aeroplan.com`, subject "Verification code to access your account". Phone SMS (***845) also available but email preferred for automation.
- ✅ **First extraction done (2026-04-15).** IAD→FRA 2026-09-16: 41 flights found, 7 Business-class flights at **70K points** written to sheet. All 7 triggered alerts (under 150K threshold). Session persists across `browser_close` — no re-login needed.
- ⛔ **Kasada (KPSDK) bot detection blocks ALL automation tools (confirmed 2026-04-17).** Kasada updated detection to catch Patchright, rebrowser-playwright, CloakBrowser, nodriver, Camoufox, and plain CDP connections. The `_abck` cookie always returns `~-1~` (invalid). See "Known blocker: Kasada" section below.
- ✅ **Chrome extension bypass working (2026-04-17).** `aeroplan-extension/` is a Chrome extension the user loads manually. It opens each of 36 combos in a tab, extracts Business fare cells from the DOM, and downloads a JSON file. The user's real Chrome passes Kasada naturally. Run `python ingest_scan.py` to write the JSON to the Google Sheet.
- ✅ **Mixed-cabin filtering working (2026-04-17).** Aeroplan shows "X% in Business Class" in a hidden `.mixed-cabin-percentage` div on fare cells. Flights ≥85% pass; below 85% are rejected. 100% Business flights have no such div. Configurable via `MIXED_CABIN_MIN_PCT` in `aeroplan_patchright.py`.

### Validated findings (from first Aeroplan run, IAD→FRA 2026-09-16)

**7 Business-class flights at 70K Aeroplan points (= 70K AMEX MR):**

All are 1-stop, operated by United (transatlantic) + Lufthansa (intra-Europe hop). Example: **UA 915** IAD→CDG (777, Polaris lie-flat) + **LH 1027** CDG→FRA (A320, 1h15m). Aeroplan DOES show mixed-cabin itineraries in the Business column — a hidden `.mixed-cabin-percentage` div indicates what percentage is actually in Business (e.g. "92% in Business Class"). See mixed-cabin filtering above.

- 09:45→07:10+1, YUL, 15h25m, AC Express + LH, 70K + CA$84 **(1 seat left)**
- 18:10→10:55+1, CDG, 10h45m, UA + LH, 70K + CA$106
- 22:30→15:50+1, CDG, 11h20m, UA + LH, 70K + CA$106
- 18:10→11:35+1, CDG, 11h25m, UA + LH, 70K + CA$106 **(7 seats)**
- 17:35→11:10+1, AMS, 11h35m, UA + LH, 70K + CA$122 **(5 seats)**
- 18:10→12:55+1, CDG, 12h45m, UA + LH, 70K + CA$106
- 17:35→12:25+1, AMS, 12h50m, UA + LH, 70K + CA$122

**Why 70K is legit:** Aeroplan uses a fixed distance-based award chart (US→Europe Business = 70K). United charges 200K and Flying Blue charges 183K for the same metal via dynamic pricing. Aeroplan is the clear winner for FRA/ZRH Business via AMEX transfer.

**4 nonstops exist but Business is "—" (not available):** 2 United (17:25, 22:10) + 2 Lufthansa (15:25, 17:55). All show 40K Economy only. LH saver Business via Aeroplan is not open for Sept 16.

**Fees are in Canadian dollars** (CA$80–CA$122 depending on routing). Roughly USD $58–$89 at current rates.

**Results page structure:** URL pattern is `aircanada.com/aeroplan/redeem/availability/outbound?org0=IAD&dest0=FRA&departureDate0=2026-09-16&ADT=1&tripType=O`. Three fare columns: Economy Class | Premium Economy | Business Class. Parse from body text by splitting on `\d+ of \d+` (flight index). Flight details available via "Details" link on each card (opens modal with leg-by-leg breakdown including flight numbers, aircraft, and duration).

### Login recipe (validated 2026-04-15)

Gigya's form ignores JS `.value` assignments and has 21 hidden submit buttons. The working approach uses Playwright's native APIs exclusively:

1. Navigate to `https://www.aircanada.com/`, click "Sign in" button.
2. Wait for `aircanada.com/clogin/pages/login` to load.
3. **Type credentials** using `browser_type` with `slowly: true` (Playwright `pressSequentially`). Label elements intercept `browser_click` on inputs, so use refs from snapshot:
   - Email: `textbox "Aeroplan number or email"`
   - Password: `textbox "Password"`
4. Click "Sign in" button via ref.
5. Wait for 2FA screen. Click "Send Code" next to the email option:
   ```js
   // via browser_evaluate
   const sendBtns = Array.from(document.querySelectorAll('button'))
     .filter(b => /^send code$/i.test((b.textContent||'').trim()));
   sendBtns.find(b => (b.parentElement.textContent||'').includes('@gmail.com')).click();
   ```
6. **Poll for OTP**: `python gmail_otp.py --poll --sender "aeroplan" --timeout 60` (from `C:\dev\jal-flights-tracker`).
7. **Enter code** using `browser_run_code` with `page.locator('input[name="emailCode_0"]').fill(code)`.
8. **Click Submit** using coordinate-based click (Gigya has 21 hidden submit buttons that break CSS selectors):
   ```js
   const codeBox = await page.locator('input[name="emailCode_0"]').boundingBox();
   await page.mouse.click(codeBox.x + codeBox.width / 2, codeBox.y + codeBox.height + 80);
   ```
9. Wait 10-12s for redirect. Page goes to `aircanada.com/home/redirect.html?code=...` then to `aircanada.com/home/us/en/aco/flights`. Header shows "Justin" + "0 pts".

**Critical:** using `page.fill()` (via `browser_run_code`) is essential — `browser_evaluate` with `.value =` does NOT update Gigya's internal state and the form rejects the submission. Similarly, coordinate-based `page.mouse.click()` is the only reliable way to hit the correct Submit button.

**Session persistence:** unknown whether the session persists across `browser_close`. Needs testing. If it doesn't, every run will need the full login + email OTP flow (~30s).

### Gigya API details (captured from network)

- **API key**: `3_zA5TRSBDlwybsx_1k8EyncAfJ2b62DJnoxPW60q4X9MqmBDJh1v_8QYaOTG8kZ8S`
- **Login endpoint**: `accounts.us1.gigya.com/accounts.login` (POST)
- **TFA endpoints**: `login.aircanada.com/accounts.tfa.*` (Air Canada's Gigya CNAME)
- **TFA flow**: `initTFA` → `email.sendVerificationCode` → `email.verifyCode` → `finalizeTFA`
- The `regToken` from login is reused across all TFA calls.

### Form structure

- **"Book with Aeroplan points"**: checkbox `#bkmg-mobile-tablet_searchTypeToggle` — requires login.
- **Trip type**: dropdown, default "Round-Trip" (need to set One-Way).
- **From / To**: airport picker fields (SFO pre-filled from geolocation).
- **Departure date / Return date**: date fields.
- **Passengers**: default 1 Adult.
- **Search**: red "Search" button.
- No cabin class selector on the form — all cabins shown in results, pick from there.

### Known blocker: Kasada (KPSDK) bot detection (2026-04-16)

Air Canada's Aeroplan search API is protected by **Kasada** (identified by `x-kpsdk-ct`, `x-kpsdk-cd`, `x-kpsdk-v` request headers and `p.js` client-side SDK at `akamai-gw.dbaas.aircanada.com/{uuid}/{uuid}/p.js`).

**What works:** Login (Gigya SSO), calendar pricing (`air-calendars` endpoint), profile/session APIs.

**What's blocked:** The `air-bounds` endpoint (`POST akamai-gw.dbaas.aircanada.com/loyalty/dapidynamicplus/1ASIUDALAC/v2/search/air-bounds`). Returns HTTP 200 with **empty body** (0 bytes) when Kasada detects Playwright. Direct Python `requests` calls without Kasada tokens return **429**.

**How Kasada detects Playwright:** Kasada's `p.js` generates challenge tokens by fingerprinting the browser at the CDP/engine level. Known signals include `UtilityScript` in `Error.stack` traces (Playwright's code injection mechanism), CDP connection artifacts, and other deep browser internals. These cannot be patched from JavaScript — they're inherent to how Playwright communicates with Chrome via the DevTools Protocol.

**Things tried (all failed):**
1. Clearing Akamai/Kasada cookies (`_abck`, `bm_sz`, `bm_sv`) → regenerated with same -1 validation
2. `page.addInitScript()` to patch `Error.stack`, `chrome.runtime`, `navigator.permissions` → sensor still detects automation
3. Simulated mouse movements, scrolling, human-like delays → no effect on `_abck` validation
4. Stripping Kasada headers (`x-kpsdk-*`) from `air-bounds` request via `page.route()` → still empty response
5. Direct Python HTTP call with captured auth tokens, no Kasada headers → 429

**Why it worked on 2026-04-15:** Unknown. Possibilities: (a) Kasada policy was rolled out between April 15–16, (b) the browser profile had cached valid Kasada tokens from an earlier manual session that expired, (c) Kasada has a grace period for new sessions that was exceeded.

**Solution: Patchright** (`pip install patchright`). Patchright is a patched fork of Playwright that avoids `Runtime.Enable` entirely by executing JS in isolated `ExecutionContexts`. It also removes `--enable-automation` and adds `--disable-blink-features=AutomationControlled`. The API is identical to Playwright's — same `page.goto()`, `page.evaluate()`, etc.

Run Aeroplan scans with `python aeroplan_patchright.py` (not through MCP). Uses its own browser profile at `.patchright-profile/` (gitignored). Login session persists across `browser_close`. United and KLM phases continue using standard Playwright MCP.

**First full scan (2026-04-16):** 36 combos, 264 Business flights captured, 109 alerts under 150K, 0 failures, 0 logins needed (session persisted). Min points: 70K (IAD→CDG). BWI combos return 0 Business flights (flights exist but no Business award seats). 3 combos had no flights at all (IAD/DCA→CDG Sept 15, BWI→FRA Sept 18).

## Tasks

Use `TaskCreate`/`TaskUpdate` for each run. Create one "Run Oktoberfest flight session" task at the start, mark in_progress, mark completed at the end.

## What NOT to do

- Never commit anything under `secrets/`, `.env`, or `.playwright-mcp/`.
- Never log `UA_PASSWORD` or the SMS 2FA code.
- Never use `mcp__playwright__browser_snapshot` on the results page — it's too large. Use `browser_evaluate` with targeted queries.
- Do not include mixed-cabin itineraries below 85% Business — filter via `.mixed-cabin-percentage` div on Aeroplan, "Mixed cabin" text on United.
- Do not include itineraries with more than 1 stop.
- Do not try to construct the award URL (`at=1&...`) yourself — submit via the form each time (server-generated `pst` token).
- Do not create new Python source files or markdown docs beyond what exists.
- Do not commit or push to git from inside a scheduled session.
- Do not expand the search grid (origins, destinations, dates, or add first class) without explicit instructions.

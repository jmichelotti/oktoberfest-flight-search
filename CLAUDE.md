# oktoberfest-flight-search

Automated scraper that runs on a schedule to find **pure-business one-way award tickets** from the Washington DC area to Europe for an Oktoberfest trip. Drives united.com via the Playwright MCP using a logged-in MileagePlus session, filters out mixed-cabin itineraries, and writes results to a Google Sheet.

## Mission

Catch pure-business-class award availability on United (MileagePlus miles — transferred 1:1 from AMEX Membership Rewards) for one-way WAS → CDG / FRA / ZRH on September 15, 16, 17, or 18, 2026. Capture data on every run so we can see trends, and alert (via Sheet append) on any pure-business itinerary at or below 150k miles.

## Constants (do not change without asking)

- **Origins:** `BWI`, `DCA`, `IAD`
- **Destinations:** `CDG`, `FRA`, `ZRH`
- **Dates:** `2026-09-15`, `2026-09-16`, `2026-09-17`, `2026-09-18`
- **Cabin (v1):** Business only (select value `2` in the home-page form once award is checked)
- **Max stops:** 1
- **Mixed-cabin policy:** reject — any card showing "Mixed cabin" is dropped
- **Alert threshold:** `ALERT_THRESHOLD_POINTS` from `.env` (default `150000`)
- **Passengers:** 1 adult
- **Trip type:** one-way
- **Source airline:** united.com MileagePlus award search (requires login)

Total per run: 3 origins × 3 destinations × 4 dates = **36 searches**.

## Architecture

Same pattern as sibling projects `jal-flights-tracker` and `pc-deal-tracker`:

- **Playwright MCP** — real Chrome, drives united.com with persistent profile at `.playwright-mcp/`
- **`sheet_client.py`** — Google Sheets client with `upsert_snapshot_bulk`, `append_history_bulk`, `append_alerts`
- **`update_sheet.py`** — CLI wrapper for `init` / debugging
- **`.env`** — `UA_USERNAME`, `UA_PASSWORD`, `ALERT_THRESHOLD_POINTS`
- **`secrets/sa.json`** — Google service account key (same SA as the sibling trackers)

## ⚠️ Login & 2FA (critical — read this first)

United's **pure award search** (`at=1` param) requires MileagePlus login. First login to this browser profile will also trigger SMS 2FA. **A human must complete the 2FA manually once, with "Remember this browser" checked**, before scheduled runs can work unattended.

Signs you're already authenticated: after any page load, `document.body.innerText` contains "Hi, Justin" and a miles balance.

If the scheduled run lands on a sign-in dialog (password field or MPIDEmailField visible), the cookie has expired. The run should:
1. Save a failure artifact (screenshot + HTML) and abort with a clear summary.
2. The next human invocation re-runs interactively, logs in, completes SMS, and checks "Remember this browser".

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

For each (origin, destination, date) combo, prefer the **form-submit** path (more reliable than constructing an award URL from scratch). The exact pattern that works:

1. Navigate to `https://www.united.com/en/us/`.
2. `document.getElementById('travelTab').click()` — activate the Book tab (form renders into the DOM on click).
3. Wait ~1s. Then fill the form programmatically via `browser_evaluate`:
   ```js
   () => {
     function setInput(el, val) {
       const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
       setter.call(el, val);
       el.dispatchEvent(new Event('input', { bubbles: true }));
       el.dispatchEvent(new Event('change', { bubbles: true }));
     }
     // One-way
     document.getElementById('radiofield-item-id-flightType-1').click();
     // Award
     const award = document.getElementById('award');
     if (!award.checked) award.click();
     // Origin + destination
     setInput(document.getElementById('bookFlightOriginInput'), '<ORIGIN>');
     setInput(document.getElementById('bookFlightDestinationInput'), '<DEST>');
   }
   ```
4. Set cabin to **Business** (value `2`) once award is enabled. The `cabinType` select changes its option set when the award checkbox is toggled — Business becomes value `2`:
   ```js
   () => {
     const cabin = document.getElementById('cabinType');
     const setter = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value').set;
     setter.call(cabin, '2');
     cabin.dispatchEvent(new Event('change', { bubbles: true }));
   }
   ```
5. Open the date picker: `document.getElementById('DepartDate_start').click()`. Then navigate to September 2026. The picker shows **2 months side-by-side**. From today's month (April 2026), click `button[aria-label="Next month"]` **4 times** to land on a view where the left month is September. Then click day 16 (or 15/17/18) by matching its button: the day button's text is `{day}{price}k` (e.g. `1640k` = Sept 16, 40k miles — calendar shows the cheapest ANY-cabin price, not business-specific). Verify the button's ancestor caption is `September 2026` before clicking.
6. Click the "Find flights" button (`button[aria-label="Find flights"]`).
7. Wait up to ~15 seconds for the results page to load. URL will contain `/fsr/choose-flights?...at=1...` — the `at=1` parameter confirms award mode. If a sign-in dialog opens instead, abort (see Step 2).
8. **Do NOT use `browser_snapshot` on the results page** — it's 20+ KB. Use `browser_evaluate` with targeted text extraction.

### Step 4 — Extract results per combo

Run this in `browser_evaluate`. It splits the results by flight block, extracts all needed fields, and filters for pure business:

```js
() => {
  const body = document.body.innerText;
  const cards = [];
  const re = /((?:NONSTOP|\d+\s+STOPS?))[\s\S]*?(?=(?:NONSTOP|\d+\s+STOPS?|$))/g;
  let m; while ((m = re.exec(body)) !== null) cards.push(m[0]);
  const parsed = cards.map(card => {
    const stopsMatch = card.match(/^(NONSTOP|\d+)\s*STOPS?/i);
    const stopsNum = !stopsMatch ? null : (stopsMatch[1].toUpperCase() === 'NONSTOP' ? 0 : parseInt(stopsMatch[1], 10));
    const dep = (card.match(/(\d{1,2}:\d{2}\s*[AP]M)\s*\nDeparting/i) || [])[1] || '';
    const arr = (card.match(/(\d{1,2}:\d{2}\s*[AP]M)\s*\nArriving/i) || [])[1] || '';
    const nextDay = /Arrives Sep/i.test(card) ? '+1' : '';
    const dur = (card.match(/(\d+H,?\s*\d*M?)\s*\nDuration/i) || [])[1] || '';
    const flights = [...card.matchAll(/(UA|LH|LX|LO|OS|SK|SN|TK|TP|AC|NH|AV|ET|CA|EW)\s*\d{1,4}/gi)]
      .map(x => x[0].replace(/\s+/,''));
    const stopAirportMatch = card.match(/Destination[\s\S]{0,100}\n([A-Z]{3})\s*\n[A-Z][a-z]/);
    const mixed = /Mixed cabin/i.test(card);
    // Capture the true award rate (post "Select fare for Business (lowest)"):
    const busMatch = card.match(/Select fare for Business[\s\S]*?(\d+)k?\s*miles\s*\+\s*(\$[\d.]+)/i);
    const busPts = busMatch ? parseInt(busMatch[1]) * 1000 : null;
    const busFees = busMatch ? busMatch[2] : '';
    return { stopsNum, dep, arr, nextDay, dur, flights, stopAirport: stopAirportMatch?.[1] || '', mixed, busPts, busFees };
  });
  // Dedupe (United sometimes repeats cards via sticky details)
  const seen = new Set();
  return parsed.filter(p => {
    if (!p.busPts || p.stopsNum === null) return false;
    if (p.mixed) return false;                 // drop mixed cabin
    if (p.stopsNum > 1) return false;          // max 1 stop
    if (!p.flights.length) return false;       // need flight numbers
    const key = p.flights.join('+') + '|' + p.dep;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}
```

Build per-flight records using this shape:

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

Airline mapping: UA→United, LH→Lufthansa, LX→Swiss, LO→LOT, OS→Austrian, SN→Brussels, SK→SAS, TK→Turkish, TP→TAP, AC→Air Canada, NH→ANA, AV→Avianca, ET→Ethiopian, CA→Air China, EW→Eurowings.

Between searches, go back to the home URL and re-run Step 3 from the top — the cart tab on the form sometimes retains previous input and causes weirdness. Do NOT close the browser between combos; that wipes state.

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

- **`Snapshot`** (one row per `Fingerprint`, upserted): `Fingerprint`, `Origin`, `Destination`, `Depart Date`, `Airline(s)`, `Stops`, `Stop Airports`, `Flight Numbers`, `Dep Time`, `Arr Time`, `Duration`, `Cabin`, `Points`, `Fees`, `Seats Left`, `Lowest Points Ever`, `Lowest Points Date Seen`, `First Seen`, `Last Scanned`.
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

- Award search URL produced after form submission:
  `https://www.united.com/en/us/fsr/choose-flights?f=IAD&t=FRA&d=2026-09-16&tt=1&at=1&sc=7&act=2&px=1&pst=<token>&taxng=1&newHP=True&clm=7&st=bestmatches&tqp=A`
  The `pst` token appears to be server-generated; do not try to construct it — always submit via form.
- IAD → FRA Sept 16 2026 Business saver = **200,000 miles + $5.60** (UA 989 nonstop 5:25 PM). Cardmember 10% off yields 180k.
- Calendar overlay prices (e.g. "40k" on Sept 15-18) are the **cheapest ANY cabin** for that day — NOT business-specific. Ignore them; always click into the date and read the Business (lowest) row.
- Lufthansa LH 419 / LH 417 appear in results but my parser didn't capture their business price — may be "Business Saver" label instead of "Business (lowest)". Iterate on the regex when we see these in production.

## Tasks

Use `TaskCreate`/`TaskUpdate` for each run. Create one "Run Oktoberfest flight session" task at the start, mark in_progress, mark completed at the end.

## What NOT to do

- Never commit anything under `secrets/`, `.env`, or `.playwright-mcp/`.
- Never log `UA_PASSWORD` or the SMS 2FA code.
- Never use `mcp__playwright__browser_snapshot` on the results page — it's too large. Use `browser_evaluate` with targeted queries.
- Do not include mixed-cabin itineraries — filter them via the "Mixed cabin" text marker.
- Do not include itineraries with more than 1 stop.
- Do not try to construct the award URL (`at=1&...`) yourself — submit via the form each time (server-generated `pst` token).
- Do not create new Python source files or markdown docs beyond what exists.
- Do not commit or push to git from inside a scheduled session.
- Do not expand the search grid (origins, destinations, dates, or add first class) without explicit instructions.

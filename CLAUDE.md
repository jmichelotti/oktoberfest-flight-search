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
- **Mixed-cabin policy:** reject — any card showing "Mixed cabin" within the Business fare section is dropped
- **Alert threshold:** `ALERT_THRESHOLD_POINTS` from `.env` (default `150000`)
- **Passengers:** 1 adult
- **Trip type:** one-way

Per airline, a full run = 3 origins × 3 destinations × 4 dates = **36 searches**.

## Architecture

Same pattern as sibling projects `jal-flights-tracker` and `pc-deal-tracker`:

- **Playwright MCP** — real Chrome with a persistent browser profile managed by MCP (NOT the `.playwright-mcp/` folder in this repo — that's only runtime logs/snapshots, safe to delete). Profile state is where auth/2FA cookies live and persists across `browser_close`.
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

## Phase 2: Air France / Flying Blue (pending credentials)

**Goal:** extend the tracker to cover Air France + KLM awards, priced in Flying Blue miles. AMEX Membership Rewards transfers to Flying Blue 1:1, same economics as UA. This closes the **CDG gap** (United has no nonstop WAS→CDG on Star Alliance; AF 55 runs IAD→CDG daily) and often surfaces lower saver pricing than UA on the same corridors (Flying Blue saver business to Europe typically 55–75k pts with monthly promos).

### Prerequisites (blocking)

- `FB_USERNAME` and `FB_PASSWORD` in `.env` — operator must fill these in before Phase 2 runs. Same Flying Blue account works on both airfrance.us and klm.com. Skip Phase 2 cleanly if either env var is empty.
- If Flying Blue 2FA is enforced on first login, a human has to complete it interactively once with "Remember this device" (or equivalent) checked, same as we did for United.

### Source

Drive **`https://wwws.airfrance.us/`** and use the on-page **Book with Miles** tab. FlyingBlue.com itself is a loyalty-marketing site with no search form — award search only lives on airfrance.us / klm.com, gated by Flying Blue login.

### Form structure (captured from snapshot 2026-04-15)

- **Tabs**: "Book a flight" (default) and "Book with Miles" — click the Miles tab to switch into award mode.
- **Trip combobox**: Round trip (default) / One way — switch to One way.
- **Cabin combobox**: Economy / Premium / Business / La Première — pick Business.
- **From / To** comboboxes: type IATA code, select from suggestions.
- **Travel dates** button: opens a calendar picker with miles-price overlay per day (like UA, but the overlay is the real Flying Blue saver price, not the cheapest-cabin price).
- **Passenger combobox**: 1 adult (default).
- **Search flights** button.

Once on the Miles tab, the page shows **"Log in to your Flying Blue account to book a ticket with Miles. Log in"** — login is mandatory. No equivalent of UA's "Money + Miles" hybrid mode.

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

### Things to watch for during first validation

- Flying Blue **monthly Promo Rewards** can drop biz to ~40–50k one-way on specific corridors, but they're date-specific. If the calendar overlay shows unusually low prices for a specific date, expand into that date and confirm the Business fare (not Economy Promo).
- AF nonstop IAD→CDG is **AF 55** (evening westbound, morning eastbound). Expect to see it prominently for our dates.
- KLM routings typically go via AMS (IAD→AMS on KL652, AMS→FRA/ZRH/CDG connections).
- CDG→FRA/ZRH rerouting: from CDG, client takes the train, so any routing landing at CDG is fine. For FRA or ZRH destinations, AF/KL may route via CDG or AMS with a connecting AF/KL flight.

### Split-vs-combined scheduling

Each phase takes ~20 min of real-time scraping. Combined session = ~40 min per scheduled run. If `claude -p` has issues with that duration under Task Scheduler, split into two scheduler tasks (UA at 08:00, AF at 08:30) using different entry prompts in `run-tracker.bat`. Keep combined for now until we see a problem.

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

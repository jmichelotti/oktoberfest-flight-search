Continue work on the oktoberfest-flight-search tracker at `C:\dev\oktoberfest-flight-search`. Read `CLAUDE.md` in full before doing anything — focus on Phase 3 (Aeroplan) which is the active area.

## Three tasks for this session

### Task 1: Handle "No flights available" and invalid airports gracefully

The Aeroplan scan script (`aeroplan_scan.js`) runs 36 combos (3 origins × 3 destinations × 4 dates). Two edge cases need clean handling:

1. **"No flights available"** — many date/route combos legitimately have zero Business award seats. The page loads correctly with text "No flights available on the date you selected". This is already partially handled (detected and skipped) but should be logged cleanly in the scan summary rather than counted as a failure.

2. **Invalid airports** — BWI combos (12 of 36) returned "unknown page state" in the last scan. Aeroplan may not serve BWI as a departure airport, or the page shows a different error. Check what BWI pages actually show, and either skip BWI entirely or handle the error message gracefully.

Fix these in `aeroplan_scan.js` (the MCP-based scan script). The standalone `aeroplan_scan.py` has a Gigya login blocker (documented in CLAUDE.md) — don't try to fix that; it's a known limitation.

### Task 2: Add CPP (cents per point) column to the sheet

**Goal:** for each award flight in the sheet, calculate how many cents each point is worth by comparing the points price to the cash price for the same route/date.

**Approach:** look up the cash fare for the same origin→destination on the same date. The simplest source is Google Flights (no login needed) or the Aeroplan results page itself (which shows Economy/Premium/Business fares — but in points, not cash). Alternatively, use a reference value: Aeroplan points are generally valued at ~1.5 cpp, but actual cpp varies by route.

**Formula:** `CPP = (Cash Price in USD) / Points × 100`

For example: if a Business class ticket costs $4,500 cash and 70,000 points + CA$106 fees, then CPP = ($4,500 - $77 fees in USD) / 70,000 × 100 = 6.3 cpp.

**Implementation:**
- Add a `CPP` column to `SNAPSHOT_COLUMNS`, `HISTORY_COLUMNS`, and `ALERT_COLUMNS` in `sheet_client.py`
- Run `python update_sheet.py init` to update headers
- **IMPORTANT:** after adding the column, shift existing data rows RIGHT by one at the new column's position — the same bug happened last time (see Task 3 in this file). Fix it immediately after adding the column.
- Either calculate CPP from a cash price lookup, or leave it as a formula in the sheet (`=IF(Points>0, (CashPrice - Fees) / Points * 100, "")`) that the user fills manually.

If a full cash price lookup is too complex for this session, an alternative is to add `Cash Price` as a blank column that the user fills in, and `CPP` as a Google Sheets formula that auto-calculates.

### Task 3: Run the full 36-combo Aeroplan grid scan

The last scan attempt found 0 Business flights across all combos because "No flights available" was the result for most routes. However, an earlier manual search for IAD→FRA Sept 16 found 41 flights with 7 at 70K Business. The discrepancy may be due to:
- Award inventory opening/closing rapidly
- The direct URL navigation loading a different search context than the form-submitted search
- A "Filter by cabin" defaulting to something restrictive

**Steps:**
1. Log in via MCP (use the recipe in CLAUDE.md Phase 3 — pressSequentially for credentials, email OTP via `gmail_otp.py --poll --sender "aeroplan"`, page.fill for code, coordinate mouse.click for Submit)
2. First, manually verify one combo works: navigate to `https://www.aircanada.com/aeroplan/redeem/availability/outbound?org0=IAD&dest0=FRA&departureDate0=2026-09-16&ADT=1&YTH=0&CHD=0&INF=0&INS=0&lang=en-CA&tripType=O&marketCode=INT` and check if "flights found" appears
3. If it does, run the full scan via `browser_run_code` with the script in `aeroplan_scan.js`
4. If it shows "No flights available" even for known-good combos, investigate the search context (maybe the "Canadian edition" dialog needs dismissing, or the session needs the Aeroplan toggle activated first)
5. Write results to the sheet including Search URLs
6. Clean up `session_results.json` after

## Key context

- **Aeroplan login only works through MCP Playwright** (not standalone). Gigya's form anti-automation blocks credentials + submit in standalone Playwright. Documented in CLAUDE.md.
- **OTP retrieval:** `cd C:\dev\jal-flights-tracker && python gmail_otp.py --poll --sender "aeroplan" --timeout 60`
- **Credentials:** `.env` has `AP_USERNAME` and `AP_PASSWORD`
- **Sheet data was just fixed** — a previous column addition shifted data without shifting existing rows. If adding new columns, shift existing row data immediately after updating headers.
- The `aeroplan_scan.py` standalone script exists but can't log in. It's useful as reference for the extraction logic but should not be run until the Gigya login issue is solved.

## When you're done

- Update CLAUDE.md with any new findings
- Commit and push
- Delete this NEXT_PROMPT.md

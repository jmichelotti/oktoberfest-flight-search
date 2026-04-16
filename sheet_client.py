"""Google Sheets client for the Oktoberfest Flight Search tracker.

Three tabs:
- Snapshot: one row per Fingerprint (Origin|Dest|Date|FlightNumbers), upserted each run.
- History: append-only log, one row per flight option per run.
- Alerts: append-only, one row whenever Points <= threshold (set in .env).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import gspread
from google.oauth2.service_account import Credentials


SNAPSHOT_COLUMNS = [
    "Fingerprint",
    "Origin",
    "Destination",
    "Depart Date",
    "Airline(s)",
    "Stops",
    "Stop Airports",
    "Flight Numbers",
    "Dep Time",
    "Arr Time",
    "Duration",
    "Cabin",
    "Points",
    "Fees",
    "Seats Left",
    "Lowest Points Ever",
    "Lowest Points Date Seen",
    "First Seen",
    "Last Scanned",
]

HISTORY_COLUMNS = [
    "Scan Time",
    "Fingerprint",
    "Origin",
    "Destination",
    "Depart Date",
    "Airline(s)",
    "Stops",
    "Stop Airports",
    "Flight Numbers",
    "Dep Time",
    "Arr Time",
    "Duration",
    "Cabin",
    "Points",
    "Fees",
    "Seats Left",
]

ALERT_COLUMNS = [
    "Scan Time",
    "Origin",
    "Destination",
    "Depart Date",
    "Airline(s)",
    "Stops",
    "Flight Numbers",
    "Dep Time",
    "Arr Time",
    "Duration",
    "Cabin",
    "Points",
    "Fees",
    "Seats Left",
    "Threshold Hit",
    "Emailed",
]

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

PROJECT_ROOT = Path(__file__).parent
CONFIG_PATH = PROJECT_ROOT / "sheet-config.json"


def _today() -> str:
    return datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d")


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")


def _load_config() -> dict:
    with CONFIG_PATH.open() as f:
        return json.load(f)


class SheetClient:
    def __init__(self):
        cfg = _load_config()
        self.cfg = cfg
        sa_path = PROJECT_ROOT / cfg["service_account_path"]
        creds = Credentials.from_service_account_file(str(sa_path), scopes=SCOPES)
        self.gc = gspread.authorize(creds)
        self.ss = self.gc.open_by_url(cfg["sheet_url"])
        self.snapshot_name = cfg["snapshot_tab"]
        self.history_name = cfg["history_tab"]
        self.alerts_name = cfg["alerts_tab"]

    def _ws(self, name: str):
        try:
            return self.ss.worksheet(name)
        except gspread.WorksheetNotFound:
            return self.ss.add_worksheet(title=name, rows=1000, cols=26)

    def init(self) -> dict:
        """Write headers + freeze + bold + native Table on every tab. Idempotent."""
        results = {}
        for tab, cols in [
            (self.snapshot_name, SNAPSHOT_COLUMNS),
            (self.history_name, HISTORY_COLUMNS),
            (self.alerts_name, ALERT_COLUMNS),
        ]:
            ws = self._ws(tab)
            existing = ws.row_values(1)
            if existing != cols:
                ws.update([cols], "A1", value_input_option="USER_ENTERED")
            self._format_header(ws, len(cols))
            self._ensure_table(ws, tab, len(cols))
            results[tab] = f"{len(cols)} columns ready"
        return results

    def _format_header(self, ws, ncols: int) -> None:
        ws.freeze(rows=1)
        ws.format(
            f"A1:{gspread.utils.rowcol_to_a1(1, ncols)}",
            {
                "textFormat": {"bold": True},
                "backgroundColor": {"red": 0.9, "green": 0.9, "blue": 0.95},
            },
        )

    def _ensure_table(self, ws, tab_name: str, ncols: int) -> None:
        try:
            body = {
                "requests": [
                    {
                        "addTable": {
                            "table": {
                                "name": f"{tab_name}Table",
                                "range": {
                                    "sheetId": ws.id,
                                    "startRowIndex": 0,
                                    "endRowIndex": 1000,
                                    "startColumnIndex": 0,
                                    "endColumnIndex": ncols,
                                },
                            }
                        }
                    }
                ]
            }
            self.ss.batch_update(body)
        except Exception:
            pass

    def _next_empty_row(self, ws) -> int:
        vals = ws.col_values(1)
        return len(vals) + 1

    def _ensure_capacity(self, ws, row: int) -> None:
        if row > ws.row_count:
            ws.add_rows(row - ws.row_count + 50)

    def read_snapshot(self) -> list[dict]:
        ws = self._ws(self.snapshot_name)
        return ws.get_all_records(expected_headers=SNAPSHOT_COLUMNS)

    def upsert_snapshot_bulk(self, cells: list[dict]) -> dict:
        """Bulk upsert snapshot rows keyed on Fingerprint.

        Each cell must include: Fingerprint, Origin, Destination, Depart Date,
        Airline(s), Stops, Stop Airports, Flight Numbers, Dep Time, Arr Time,
        Duration, Cabin, Points (int), Fees, Seats Left.
        Computes / updates Lowest Points Ever, Lowest Points Date Seen,
        First Seen, Last Scanned automatically.
        """
        ws = self._ws(self.snapshot_name)
        today = _today()
        now = _now()

        existing_rows = ws.get_all_records(expected_headers=SNAPSHOT_COLUMNS)
        existing_by_key: dict[str, tuple[int, dict]] = {}
        for idx, row in enumerate(existing_rows, start=2):
            key = str(row.get("Fingerprint", ""))
            if key:
                existing_by_key[key] = (idx, row)

        updates: list[dict] = []
        inserts: list[list] = []
        inserted_count = 0
        updated_count = 0

        for cell in cells:
            fp = cell["Fingerprint"]
            new_pts = _to_int(cell.get("Points"))

            if fp in existing_by_key:
                row_idx, row = existing_by_key[fp]
                prev_low = _to_int(row.get("Lowest Points Ever"))
                prev_low_date = str(row.get("Lowest Points Date Seen") or "")
                first_seen = str(row.get("First Seen") or today)

                if new_pts > 0 and (prev_low == 0 or new_pts < prev_low):
                    low_pts = new_pts
                    low_date = today
                else:
                    low_pts = prev_low if prev_low > 0 else new_pts
                    low_date = prev_low_date or today

                new_row = _snapshot_row(cell, new_pts, low_pts, low_date, first_seen, now)
                end_a1 = gspread.utils.rowcol_to_a1(row_idx, len(SNAPSHOT_COLUMNS))
                updates.append({
                    "range": f"A{row_idx}:{end_a1}",
                    "values": [new_row],
                })
                updated_count += 1
            else:
                low_pts = new_pts if new_pts > 0 else 0
                low_date = today if new_pts > 0 else ""
                new_row = _snapshot_row(cell, new_pts, low_pts, low_date, today, now)
                inserts.append(new_row)
                inserted_count += 1

        if updates:
            ws.batch_update(updates, value_input_option="USER_ENTERED")

        if inserts:
            start_row = self._next_empty_row(ws)
            self._ensure_capacity(ws, start_row + len(inserts))
            end_row = start_row + len(inserts) - 1
            end_a1 = gspread.utils.rowcol_to_a1(end_row, len(SNAPSHOT_COLUMNS))
            ws.update(inserts, f"A{start_row}:{end_a1}",
                      value_input_option="USER_ENTERED")

        return {"inserted": inserted_count, "updated": updated_count}

    def append_history_bulk(self, cells: list[dict]) -> dict:
        """Append raw scan rows to History tab."""
        if not cells:
            return {"appended": 0}
        ws = self._ws(self.history_name)
        now = _now()
        rows = []
        for cell in cells:
            rows.append([
                now,
                cell.get("Fingerprint", ""),
                cell.get("Origin", ""),
                cell.get("Destination", ""),
                cell.get("Depart Date", ""),
                cell.get("Airline(s)", ""),
                cell.get("Stops", ""),
                cell.get("Stop Airports", ""),
                cell.get("Flight Numbers", ""),
                cell.get("Dep Time", ""),
                cell.get("Arr Time", ""),
                cell.get("Duration", ""),
                cell.get("Cabin", ""),
                _to_int(cell.get("Points")) or "",
                cell.get("Fees", ""),
                cell.get("Seats Left", ""),
            ])
        start_row = self._next_empty_row(ws)
        self._ensure_capacity(ws, start_row + len(rows))
        end_row = start_row + len(rows) - 1
        end_a1 = gspread.utils.rowcol_to_a1(end_row, len(HISTORY_COLUMNS))
        ws.update(rows, f"A{start_row}:{end_a1}",
                  value_input_option="USER_ENTERED")
        return {"appended": len(rows)}

    def append_alerts(self, alerts: list[dict]) -> dict:
        """Append alert rows when Points <= threshold."""
        if not alerts:
            return {"appended": 0}
        ws = self._ws(self.alerts_name)
        now = _now()
        rows = []
        for a in alerts:
            rows.append([
                now,
                a.get("Origin", ""),
                a.get("Destination", ""),
                a.get("Depart Date", ""),
                a.get("Airline(s)", ""),
                a.get("Stops", ""),
                a.get("Flight Numbers", ""),
                a.get("Dep Time", ""),
                a.get("Arr Time", ""),
                a.get("Duration", ""),
                a.get("Cabin", ""),
                _to_int(a.get("Points")) or "",
                a.get("Fees", ""),
                a.get("Seats Left", ""),
                a.get("Threshold Hit", ""),
                "",
            ])
        start_row = self._next_empty_row(ws)
        self._ensure_capacity(ws, start_row + len(rows))
        end_row = start_row + len(rows) - 1
        end_a1 = gspread.utils.rowcol_to_a1(end_row, len(ALERT_COLUMNS))
        ws.update(rows, f"A{start_row}:{end_a1}",
                  value_input_option="USER_ENTERED")
        return {"appended": len(rows)}


def _snapshot_row(cell: dict, pts: int, low_pts: int, low_date: str,
                  first_seen: str, now: str) -> list:
    return [
        cell.get("Fingerprint", ""),
        cell.get("Origin", ""),
        cell.get("Destination", ""),
        cell.get("Depart Date", ""),
        cell.get("Airline(s)", ""),
        cell.get("Stops", ""),
        cell.get("Stop Airports", ""),
        cell.get("Flight Numbers", ""),
        cell.get("Dep Time", ""),
        cell.get("Arr Time", ""),
        cell.get("Duration", ""),
        cell.get("Cabin", ""),
        pts if pts > 0 else "",
        cell.get("Fees", ""),
        cell.get("Seats Left", ""),
        low_pts if low_pts > 0 else "",
        low_date if low_pts > 0 else "",
        first_seen,
        now,
    ]


def _to_int(value) -> int:
    if value is None or value == "":
        return 0
    try:
        return int(str(value).replace(",", "").strip())
    except (ValueError, TypeError):
        return 0

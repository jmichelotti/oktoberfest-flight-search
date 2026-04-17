"""Ingest a JSON file from the Aeroplan browser extension and write to Google Sheet.

Usage:
    python ingest_scan.py path/to/aeroplan-scan-2026-04-16T12-00-00.json

Reads the JSON, optionally looks up cash prices, then writes to the sheet
(Snapshot upsert + History append + Alerts).
"""
import json
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PROJECT_ROOT)


def load_env():
    env = {}
    env_path = os.path.join(PROJECT_ROOT, ".env")
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                if "=" in line:
                    k, v = line.strip().split("=", 1)
                    env[k] = v
    return env


def main():
    if len(sys.argv) >= 2:
        json_path = sys.argv[1]
    else:
        scans_dir = os.path.join(PROJECT_ROOT, "scans")
        downloads_dir = os.path.expanduser("~/Downloads")
        candidates = []
        for d in [scans_dir, downloads_dir]:
            if os.path.isdir(d):
                candidates.extend(
                    os.path.join(d, f) for f in os.listdir(d)
                    if f.startswith("aeroplan-scan-") and f.endswith(".json")
                )
        if not candidates:
            print("No scan files found in scans/ or ~/Downloads.")
            print("Usage: python ingest_scan.py <scan-results.json>")
            sys.exit(1)
        json_path = max(candidates, key=os.path.getmtime)
        print(f"Auto-selected latest scan: {json_path}")

    if not os.path.exists(json_path):
        print(f"File not found: {json_path}")
        sys.exit(1)

    # Copy to scans/ for historical record
    scans_dir = os.path.join(PROJECT_ROOT, "scans")
    os.makedirs(scans_dir, exist_ok=True)
    scan_basename = os.path.basename(json_path)
    scan_dest = os.path.join(scans_dir, scan_basename)
    if os.path.abspath(json_path) != os.path.abspath(scan_dest):
        import shutil
        shutil.copy2(json_path, scan_dest)
        print(f"Copied to {scan_dest}")

    with open(json_path) as f:
        results = json.load(f)

    print(f"Loaded {len(results)} flights from {json_path}")

    if not results:
        print("No flights to process.")
        return

    env = load_env()
    threshold = int(env.get("ALERT_THRESHOLD_POINTS", "150000"))

    # Optional: look up cash prices
    try:
        from aeroplan_patchright import lookup_cash_prices, enrich_with_cash_prices
        cash_data = lookup_cash_prices(results)
        enrich_with_cash_prices(results, cash_data)
        cpp_values = [r["CPP"] for r in results if r.get("CPP")]
        if cpp_values:
            print(f"CPP range: {min(cpp_values):.1f} - {max(cpp_values):.1f} cents/point")
    except Exception as e:
        print(f"Cash price lookup skipped: {e}")

    # Write to sheet
    from sheet_client import SheetClient
    client = SheetClient()

    print(f"\nWriting {len(results)} results to sheet...")
    print("Snapshot:", client.upsert_snapshot_bulk(results))
    print("History:", client.append_history_bulk(results))

    alerts = [
        {**c, "Threshold Hit": f"Under {threshold // 1000}k"}
        for c in results
        if (c.get("Points") or 0) <= threshold
    ]
    print("Alerts:", client.append_alerts(alerts))
    print(f"Alert count: {len(alerts)}")

    # Summary
    min_pts = min(r["Points"] for r in results)
    min_flight = next(r for r in results if r["Points"] == min_pts)
    mixed = [r for r in results if r.get("Business Pct", 100) < 100]

    print(f"\n{'='*50}")
    print(f"SCAN SUMMARY")
    print(f"{'='*50}")
    print(f"Total Business flights: {len(results)}")
    print(f"Min points: {min_pts:,} ({min_flight['Origin']}->{min_flight['Destination']} {min_flight['Depart Date']})")
    print(f"Alerts (under {threshold // 1000}k): {len(alerts)}")
    if mixed:
        print(f"Mixed-cabin flights included (>=85%): {len(mixed)}")


if __name__ == "__main__":
    main()

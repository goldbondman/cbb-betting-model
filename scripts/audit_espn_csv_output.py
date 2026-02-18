#!/usr/bin/env python3
"""
Audit ESPN CSV pipeline output.

Checks that all expected CSV files exist, have valid headers, and contain data rows.
Returns exit code 0 when all critical files pass, exit code 1 otherwise.
Prints a summary table suitable for GitHub Actions logs.

Usage:
    python scripts/audit_espn_csv_output.py [--csv-dir ESPN/CSV]
"""

import argparse
import csv
import os
import sys


# Expected CSV files with minimum row thresholds.
# "critical" files block the workflow when missing/empty.
# "optional" files only emit warnings.
EXPECTED_FILES = [
    {"file": "espn_games.csv", "min_rows": 1, "level": "critical"},
    {"file": "espn_teams.csv", "min_rows": 1, "level": "critical"},
    {"file": "espn_team_game_logs.csv", "min_rows": 1, "level": "critical"},
    {"file": "espn_team_game_features.csv", "min_rows": 1, "level": "critical"},
    {"file": "espn_matchups_model_ready.csv", "min_rows": 1, "level": "critical"},
    {"file": "espn_player_boxscores.csv", "min_rows": 1, "level": "important"},
    {"file": "espn_injuries.csv", "min_rows": 0, "level": "optional"},
    {"file": "espn_dq_audit.csv", "min_rows": 0, "level": "optional"},
    {"file": "espn_feature_diagnostics.csv", "min_rows": 0, "level": "optional"},
]


def audit_csv(csv_dir: str, spec: dict) -> dict:
    """Audit a single CSV file against its spec.

    Returns a result dict with keys: file, exists, header_ok, row_count,
    level, passed, reason.
    """
    path = os.path.join(csv_dir, spec["file"])
    result = {
        "file": spec["file"],
        "level": spec["level"],
        "exists": False,
        "header_ok": False,
        "row_count": 0,
        "passed": False,
        "reason": "",
    }

    if not os.path.exists(path):
        result["reason"] = "file not found"
        return result

    result["exists"] = True
    size = os.path.getsize(path)
    if size == 0:
        result["reason"] = "file is empty (0 bytes)"
        return result

    try:
        with open(path, newline="", encoding="utf-8") as fh:
            reader = csv.reader(fh)
            header = next(reader, None)
            if header is None or len(header) == 0:
                result["reason"] = "no header row"
                return result
            result["header_ok"] = True
            row_count = sum(1 for _ in reader)
            result["row_count"] = row_count
    except Exception as exc:
        result["reason"] = f"read error: {exc}"
        return result

    if result["row_count"] < spec["min_rows"]:
        result["reason"] = (
            f"row count {result['row_count']} < minimum {spec['min_rows']}"
        )
        return result

    result["passed"] = True
    return result


def run_audit(csv_dir: str) -> list:
    """Run the full audit and return list of result dicts."""
    return [audit_csv(csv_dir, spec) for spec in EXPECTED_FILES]


def print_summary(results: list) -> None:
    """Print a human-readable summary table."""
    col_w = max(len(r["file"]) for r in results) + 2
    header = f"{'File':<{col_w}} {'Level':<10} {'Exists':<8} {'Header':<8} {'Rows':<8} {'Status':<8}"
    print(header)
    print("-" * len(header))
    for r in results:
        status = "PASS" if r["passed"] else "FAIL"
        exists = "yes" if r["exists"] else "NO"
        hdr = "yes" if r["header_ok"] else "NO"
        rows = str(r["row_count"]) if r["exists"] else "-"
        print(f"{r['file']:<{col_w}} {r['level']:<10} {exists:<8} {hdr:<8} {rows:<8} {status:<8}")
        if not r["passed"]:
            print(f"  -> {r['reason']}")


def main(argv: list | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit ESPN CSV output")
    parser.add_argument(
        "--csv-dir",
        default=os.path.join("ESPN", "CSV"),
        help="Path to the CSV directory (default: ESPN/CSV)",
    )
    args = parser.parse_args(argv)

    results = run_audit(args.csv_dir)
    print_summary(results)

    critical_failures = [r for r in results if not r["passed"] and r["level"] == "critical"]
    important_failures = [r for r in results if not r["passed"] and r["level"] == "important"]

    if critical_failures:
        names = ", ".join(r["file"] for r in critical_failures)
        print(f"\n[ERROR] {len(critical_failures)} critical CSV(s) failed audit: {names}")
        return 1

    if important_failures:
        names = ", ".join(r["file"] for r in important_failures)
        print(f"\n[WARN] {len(important_failures)} important CSV(s) failed audit: {names}")

    print("\n[OK] All critical CSVs passed audit.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
import csv
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from load_csv_to_db import _prepare_csv_for_load, _preflight_validate_csv


def _run(cmd: list[str]) -> int:
    print(f"[SMOKE] {' '.join(cmd)}")
    return subprocess.call(cmd)


def main() -> int:
    code = _run([sys.executable, "-m", "compileall", "."])
    if code != 0:
        return code

    try:
        import shutil

        if shutil.which("ruff"):
            code = _run(["ruff", "check", "."])
            if code != 0:
                return code
        else:
            print("[SMOKE] ruff not installed; skipping lint")
    except Exception as exc:
        print(f"[SMOKE] ruff check skipped: {exc}")

    with TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "predictions_latest.csv"
        with path.open("w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(
                [
                    "event_id",
                    "team_id_home",
                    "team_id_away",
                    "team_home",
                    "team_away",
                    "game_datetime_utc",
                    "pred_margin_home",
                    "pred_total",
                    "model_version",
                ]
            )
            writer.writerow(
                [
                    "401813366",
                    "315",
                    "2099",
                    "Niagara Purple Eagles",
                    "Canisius Golden Griffins",
                    "2026-02-03 23:30:00+00:00",
                    "9.0",
                    "121.0",
                    "ml-linear-v1",
                ]
            )

        prepared = _prepare_csv_for_load(path, "predictions_latest")
        _preflight_validate_csv(prepared, "predictions_latest")

    print("[SMOKE] ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

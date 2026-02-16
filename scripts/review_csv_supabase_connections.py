#!/usr/bin/env python3
"""Review CSV artifacts against load_csv_to_db mappings and table specs.

Checks:
- Every discovered CSV is mapped to a destination table.
- Required columns in TABLE_SPECS are either present in CSV header or defaultable.
- Empty headers are flagged as rejected candidates.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
import sys
from typing import Dict, List, Sequence, Tuple

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from load_csv_to_db import (  # noqa: E402
    FILES_ESPN,
    FILES_ML,
    FILES_TORVIK,
    TABLE_SPECS,
)

CSV_GLOBS: Sequence[str] = ("*.csv", "ESPN/CSV/*.csv")
DEFAULTABLE_REQUIRED = {"source", "pulled_at_utc", "parse_version"}


@dataclass
class CsvReview:
    path: Path
    mapped: bool
    table: str | None
    header_cols: List[str]
    missing_required: List[str]
    status: str


def _discover_csvs() -> List[Path]:
    files: List[Path] = []
    for pattern in CSV_GLOBS:
        files.extend(ROOT.glob(pattern))
    return sorted({f for f in files if f.is_file()})


def _mapping() -> Dict[Path, str]:
    mapping: Dict[Path, str] = {}
    for rel_path, table in [*FILES_ESPN, *FILES_TORVIK, *FILES_ML]:
        base = Path("ESPN") if rel_path.startswith("CSV/") else Path(".")
        mapping[(base / rel_path).resolve()] = table
    return mapping


def _read_header(path: Path) -> List[str]:
    with path.open("r", newline="", encoding="utf-8", errors="replace") as f:
        try:
            header = next(csv.reader(f))
        except StopIteration:
            return []
    return [h.strip() for h in header if h.strip()]


def main() -> None:
    mapping = _mapping()
    reviews: List[CsvReview] = []
    discovered = {p.resolve() for p in _discover_csvs()}

    for csv_path in sorted(discovered):
        csv_path = Path(csv_path)
        resolved = csv_path.resolve()
        table = mapping.get(resolved)
        header = _read_header(csv_path)

        if not header:
            reviews.append(
                CsvReview(
                    path=csv_path,
                    mapped=table is not None,
                    table=table,
                    header_cols=header,
                    missing_required=[],
                    status="rejected-empty-header",
                )
            )
            continue

        if table is None:
            reviews.append(
                CsvReview(
                    path=csv_path,
                    mapped=False,
                    table=None,
                    header_cols=header,
                    missing_required=[],
                    status="unmapped",
                )
            )
            continue

        spec = TABLE_SPECS.get(table, {})
        required = set(spec.get("required_cols", []))
        missing_required = sorted(c for c in required if c not in header and c not in DEFAULTABLE_REQUIRED)
        status = "ready" if not missing_required else "partial-missing-required"

        reviews.append(
            CsvReview(
                path=csv_path,
                mapped=True,
                table=table,
                header_cols=header,
                missing_required=missing_required,
                status=status,
            )
        )

    for expected_path, expected_table in sorted(mapping.items(), key=lambda item: str(item[0])):
        if expected_path in discovered:
            continue
        reviews.append(
            CsvReview(
                path=expected_path,
                mapped=True,
                table=expected_table,
                header_cols=[],
                missing_required=[],
                status="missing-local-file",
            )
        )

    pulled = len(reviews)
    ready = sum(1 for r in reviews if r.status == "ready")
    partial = sum(1 for r in reviews if r.status == "partial-missing-required")
    rejected = sum(1 for r in reviews if r.status == "rejected-empty-header")
    unmapped = sum(1 for r in reviews if r.status == "unmapped")
    missing_files = sum(1 for r in reviews if r.status == "missing-local-file")

    print("CSV ↔ Supabase connection review")
    print(
        "counts "
        f"pulled={pulled} ready={ready} partial={partial} rejected={rejected} "
        f"unmapped={unmapped} missing_files={missing_files}"
    )
    print()

    for review in reviews:
        rel_path = review.path.relative_to(ROOT) if review.path.is_relative_to(ROOT) else review.path
        line = f"- [{review.status}] {rel_path}"
        if review.table:
            line += f" -> raw.{review.table}"
        print(line)
        if review.missing_required:
            print(f"    missing_required={review.missing_required}")


if __name__ == "__main__":
    main()

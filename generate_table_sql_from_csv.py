#!/usr/bin/env python3
import csv
import sys
from pathlib import Path

def quote_ident(name: str) -> str:
    # Quote if it starts with a digit or has nonstandard chars
    if not name:
        return '"_"'
    needs = (name[0].isdigit() or any(c in name for c in [' ', '-', '.', '/', ':']))
    if needs or name.lower() != name or name in ("date", "user", "order"):
        return '"' + name.replace('"', '""') + '"'
    return name

def main():
    if len(sys.argv) < 3:
        print("Usage: python generate_table_sql_from_csv.py <csv_path> <schema.table>")
        sys.exit(1)

    csv_path = Path(sys.argv[1])
    target = sys.argv[2]
    if "." not in target:
        print("Target must be schema.table")
        sys.exit(1)

    schema, table = target.split(".", 1)

    with csv_path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.reader(f)
        header = next(reader, None)

    if not header:
        print("CSV appears empty or missing header.")
        sys.exit(1)

    cols = [h.strip() for h in header if h.strip()]
    if not cols:
        print("Header row had no columns.")
        sys.exit(1)

    # Ingest-safe types: text for most, then tighten later
    col_lines = []
    for c in cols:
        col_lines.append(f"  {quote_ident(c)} text")

    # Heuristic: if row_hash exists, use it as PK
    pk = None
    for candidate in ("row_hash", "id", "game_id"):
        if candidate in cols:
            pk = candidate
            break

    print(f"create table if not exists {quote_ident(schema)}.{quote_ident(table)} (")
    print(",\n".join(col_lines) + ("," if pk else ""))
    if pk:
        print(f"  primary key ({quote_ident(pk)})")
    print(");")

if __name__ == "__main__":
    main()

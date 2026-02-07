#!/usr/bin/env python3
import os
import pandas as pd
import numpy as np

PATH = os.getenv("CSV_PATH", "espn_team_game_features.csv")
NROWS = int(os.getenv("NROWS", "20000"))

def main():
    df = pd.read_csv(PATH, nrows=NROWS, low_memory=False)
    n = len(df)
    print(f"rows_sampled={n} cols={df.shape[1]}")

    rows = []
    for c in df.columns:
        s = df[c]
        missing = float(s.isna().mean())

        # numeric-like
        if pd.api.types.is_numeric_dtype(s):
            uniq = int(s.nunique(dropna=True))
            rows.append({
                "col": c,
                "dtype": str(s.dtype),
                "missing_pct": round(missing*100, 2),
                "nunique": uniq,
                "mean_strlen": 0,
                "p95_strlen": 0,
                "max_strlen": 0,
                "est_bytes_per_row": 8 if "float" in str(s.dtype) or "int" in str(s.dtype) else 8,
            })
            continue

        # treat as string/object
        s2 = s.astype("string")
        lens = s2.dropna().str.len()
        if lens.empty:
            mean_len = p95 = mx = 0
        else:
            mean_len = float(lens.mean())
            p95 = float(lens.quantile(0.95))
            mx = int(lens.max())

        # very rough bytes estimate for text: avg_len + overhead
        est = (mean_len + 8) if mean_len else 0

        rows.append({
            "col": c,
            "dtype": str(s.dtype),
            "missing_pct": round(missing*100, 2),
            "nunique": int(s.nunique(dropna=True)),
            "mean_strlen": round(mean_len, 2),
            "p95_strlen": round(p95, 2),
            "max_strlen": mx,
            "est_bytes_per_row": round(est, 2),
        })

    prof = pd.DataFrame(rows).sort_values("est_bytes_per_row", ascending=False)
    print("\nTop 30 fattest columns (rough):")
    print(prof.head(30).to_string(index=False))

    print("\nTop 30 sparsest columns:")
    print(prof.sort_values("missing_pct", ascending=False).head(30).to_string(index=False))

    out = "tmp_features_profile.csv"
    prof.to_csv(out, index=False)
    print(f"\nWrote: {out}")

if __name__ == "__main__":
    main()

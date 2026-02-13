import pandas as pd
import numpy as np

df = pd.read_csv("ml/model_features.csv")

feature_cols = [
    c for c in df.columns
    if "_pre" in c and not c.startswith("actual_")
]

report = []

for col in feature_cols:
    vals = pd.to_numeric(df[col], errors="coerce")
    non_null = vals.notna().sum()
    std = vals.std()

    report.append({
        "feature": col,
        "non_null_pct": non_null / len(df),
        "std": std
    })

rep = pd.DataFrame(report)
rep = rep.sort_values("std", ascending=False)

rep.to_csv("ml/feature_variance_report.csv", index=False)
print(rep.head(30))

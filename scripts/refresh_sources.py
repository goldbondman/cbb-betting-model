import os
import pandas as pd

def normalize_cols(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [str(c).strip().lower().replace(" ", "_").replace(".", "") for c in df.columns]
    return df

def atomic_write_csv(df: pd.DataFrame, out_path: str) -> None:
    tmp = out_path + ".tmp"
    df.to_csv(tmp, index=False)
    os.replace(tmp, out_path)

def fetch_table(url: str, table_index: int = 0) -> pd.DataFrame:
    # Pull the first (or specified) HTML table on the page
    tables = pd.read_html(url)
    if not tables:
        raise ValueError(f"No HTML tables found at: {url}")
    if table_index >= len(tables):
        raise ValueError(f"table_index {table_index} out of range, found {len(tables)} tables at: {url}")
    return tables[table_index]

def refresh_barttorvik(out_path: str, url: str, table_index: int) -> None:
    df = fetch_table(url, table_index=table_index)
    df = normalize_cols(df)

    # Optional convenience: compute adjem if adjoe/adjde exist
    if "adjoe" in df.columns and "adjde" in df.columns and "adjem" not in df.columns:
        df["adjem"] = df["adjoe"] - df["adjde"]

    if "team" not in df.columns:
        raise ValueError("BartTorvik table missing required column: team")

    atomic_write_csv(df, out_path)

def refresh_haslametrics(out_path: str, url: str, table_index: int) -> None:
    df = fetch_table(url, table_index=table_index)
    df = normalize_cols(df)

    if "team" not in df.columns:
        raise ValueError("Haslametrics table missing required column: team")

    atomic_write_csv(df, out_path)

def main():
    torvik_url = os.environ.get("TORVIK_URL", "").strip()
    hasla_url = os.environ.get("HASLA_URL", "").strip()

    # Table indexes default to 0, adjust if the page has multiple tables
    torvik_table_index = int(os.environ.get("TORVIK_TABLE_INDEX", "0"))
    hasla_table_index = int(os.environ.get("HASLA_TABLE_INDEX", "0"))

    if not torvik_url:
        raise ValueError("Missing TORVIK_URL env var")
    if not hasla_url:
        raise ValueError("Missing HASLA_URL env var")

    refresh_barttorvik("barttorvik.csv", torvik_url, torvik_table_index)
    refresh_haslametrics("haslametrics.csv", hasla_url, hasla_table_index)

    print("OK: refreshed barttorvik.csv and haslametrics.csv")

if __name__ == "__main__":
    main()

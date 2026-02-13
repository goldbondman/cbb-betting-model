#!/usr/bin/env python3
"""
Build a slim raw.espn_team_game_core table from raw.espn_team_game_logs.

Purpose:
- Keep only essential raw box score + score columns in DB.
- Move rolling/derived feature generation to Python (ml/feature_matrix.py).

Integrity checks:
- Drops rows missing required identifiers/datetime.
- Deterministic dedupe by (event_id, team_id, home_away) keeping latest pulled_at_utc.
- Logs pulled/upserted/rejected counts.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone

import pandas as pd
import psycopg

DB_URL = (os.getenv("SUPABASE_DB_URL") or "").strip()
RAW_SCHEMA = (os.getenv("RAW_SCHEMA") or "raw").strip()
SRC_TABLE = (os.getenv("RAW_TEAM_LOGS_TABLE") or "espn_team_game_logs").strip()
DST_TABLE = (os.getenv("RAW_TEAM_CORE_TABLE") or "espn_team_game_core").strip()
SOURCE_NAME = (os.getenv("SOURCE") or "espn").strip()


@dataclass
class Counts:
    pulled: int = 0
    upserted: int = 0
    rejected: int = 0


def _must_env() -> None:
    if not DB_URL:
        raise RuntimeError("SUPABASE_DB_URL is required")


def _quote_ident(v: str) -> str:
    return '"' + v.replace('"', '""') + '"'


def main() -> None:
    _must_env()
    qsrc = f'{_quote_ident(RAW_SCHEMA)}.{_quote_ident(SRC_TABLE)}'
    qdst = f'{_quote_ident(RAW_SCHEMA)}.{_quote_ident(DST_TABLE)}'

    sql = f"""
      select
        cast(event_id as text) as event_id,
        cast(team_id as text) as team_id,
        cast(team as text) as team,
        lower(trim(cast(home_away as text))) as home_away,
        game_datetime_utc,
        points_for,
        points_against,
        fgm, fga, tpm, tpa, ftm, fta, tov, orb, drb,
        pulled_at_utc,
        source,
        parse_version
      from {qsrc}
      where event_id is not null
        and team_id is not null
        and game_datetime_utc is not null
    """

    counts = Counts()

    with psycopg.connect(DB_URL) as conn:
        df = pd.read_sql(sql, conn)
        counts.pulled = len(df)
        if df.empty:
            print("[WARN] No rows pulled from source logs")
            return

        before = len(df)
        df = df[df["home_away"].isin(["home", "away"])].copy()
        counts.rejected += before - len(df)

        df["pulled_at_utc"] = pd.to_datetime(df["pulled_at_utc"], utc=True, errors="coerce")
        df["game_datetime_utc"] = pd.to_datetime(df["game_datetime_utc"], utc=True, errors="coerce")
        df = df.dropna(subset=["event_id", "team_id", "home_away", "game_datetime_utc"])

        sort_cols = ["event_id", "team_id", "home_away", "pulled_at_utc"]
        for c in sort_cols:
            if c not in df.columns:
                df[c] = pd.NaT
        df = df.sort_values(sort_cols, ascending=[True, True, True, True])
        df = df.drop_duplicates(subset=["event_id", "team_id", "home_away"], keep="last")

        now = datetime.now(timezone.utc).isoformat()
        records = []
        cols = [
            "event_id",
            "team_id",
            "team",
            "home_away",
            "game_datetime_utc",
            "points_for",
            "points_against",
            "fgm", "fga", "tpm", "tpa", "ftm", "fta", "tov", "orb", "drb",
            "pulled_at_utc",
            "source",
            "parse_version",
            "verification_status",
            "verification_notes",
        ]

        for _, r in df.iterrows():
            records.append(
                (
                    str(r.get("event_id") or ""),
                    str(r.get("team_id") or ""),
                    str(r.get("team") or ""),
                    str(r.get("home_away") or ""),
                    r.get("game_datetime_utc"),
                    r.get("points_for"),
                    r.get("points_against"),
                    r.get("fgm"), r.get("fga"), r.get("tpm"), r.get("tpa"), r.get("ftm"), r.get("fta"), r.get("tov"), r.get("orb"), r.get("drb"),
                    r.get("pulled_at_utc"),
                    str(r.get("source") or SOURCE_NAME),
                    str(r.get("parse_version") or "v1"),
                    "verified",
                    f"built_from_{SRC_TABLE}_at_{now}",
                )
            )

        upsert_sql = f"""
            insert into {qdst} (
              event_id, team_id, team, home_away, game_datetime_utc,
              points_for, points_against,
              fgm, fga, tpm, tpa, ftm, fta, tov, orb, drb,
              pulled_at_utc, source, parse_version,
              verification_status, verification_notes
            )
            values (
              %s,%s,%s,%s,%s,
              %s,%s,
              %s,%s,%s,%s,%s,%s,%s,%s,%s,
              %s,%s,%s,
              %s,%s
            )
            on conflict (event_id, team_id, home_away)
            do update set
              team = excluded.team,
              game_datetime_utc = excluded.game_datetime_utc,
              points_for = excluded.points_for,
              points_against = excluded.points_against,
              fgm = excluded.fgm,
              fga = excluded.fga,
              tpm = excluded.tpm,
              tpa = excluded.tpa,
              ftm = excluded.ftm,
              fta = excluded.fta,
              tov = excluded.tov,
              orb = excluded.orb,
              drb = excluded.drb,
              pulled_at_utc = excluded.pulled_at_utc,
              source = excluded.source,
              parse_version = excluded.parse_version,
              verification_status = excluded.verification_status,
              verification_notes = excluded.verification_notes;
        """

        with conn.cursor() as cur:
            cur.executemany(upsert_sql, records)
        conn.commit()
        counts.upserted = len(records)

    print(
        f"[OK] {RAW_SCHEMA}.{DST_TABLE}: pulled={counts.pulled} upserted={counts.upserted} rejected={counts.rejected}"
    )


if __name__ == "__main__":
    main()

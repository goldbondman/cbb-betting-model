import csv
from pathlib import Path

from load_csv_to_db import (
    _prepare_csv_for_load,
    _preflight_validate_csv,
    _resolve_upsert_conflict_columns,
)


def test_predictions_latest_row_hash_generated(tmp_path: Path) -> None:
    path = tmp_path / "predictions_latest.csv"
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
                "pulled_at_utc",
                "source",
                "parse_version",
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
                "2026-02-04 01:00:00+00:00",
                "model",
                "v1.0.0",
            ]
        )

    prepared = _prepare_csv_for_load(path, "predictions_latest")
    _preflight_validate_csv(prepared, "predictions_latest")

    with prepared.open("r", newline="") as f:
        reader = csv.reader(f)
        header = next(reader)
        row = next(reader)

    assert header[0] == "row_hash"
    assert row[0] != ""


def test_espn_player_boxscores_row_hash_generated(tmp_path: Path) -> None:
    path = tmp_path / "espn_player_boxscores.csv"
    with path.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "event_id",
                "game_datetime_utc",
                "team_id",
                "team",
                "home_away",
                "athlete_id",
                "player",
                "starter",
                "min",
                "pts",
                "fgm",
                "fga",
                "tpm",
                "tpa",
                "ftm",
                "fta",
                "reb",
                "orb",
                "drb",
                "ast",
                "stl",
                "blk",
                "tov",
                "pf",
                "pulled_at_utc",
                "source",
                "parse_version",
            ]
        )
        writer.writerow(
            [
                "401813366",
                "2026-02-03 23:30:00+00:00",
                "315",
                "Niagara Purple Eagles",
                "home",
                "4433120",
                "John Doe",
                "1",
                "32",
                "18",
                "7",
                "14",
                "2",
                "5",
                "2",
                "3",
                "6",
                "2",
                "4",
                "3",
                "1",
                "1",
                "2",
                "3",
                "2026-02-04 01:00:00+00:00",
                "espn",
                "v1.4.2",
            ]
        )

    prepared = _prepare_csv_for_load(path, "espn_player_boxscores")
    _preflight_validate_csv(prepared, "espn_player_boxscores")

    with prepared.open("r", newline="") as f:
        reader = csv.reader(f)
        header = next(reader)
        row = next(reader)

    assert header[0] == "row_hash"
    assert row[0] != ""


def test_resolve_upsert_conflict_columns_falls_back_to_row_hash_keys() -> None:
    cols = _resolve_upsert_conflict_columns(
        "espn_player_boxscores",
        pk_cols=[],
        csv_cols=["event_id", "team_id", "athlete_id", "player"],
        table_cols=["event_id", "team_id", "athlete_id", "player", "row_hash"],
    )
    assert cols == ["event_id", "team_id", "athlete_id"]


def test_resolve_upsert_conflict_columns_prefers_natural_key_over_row_hash_pk() -> None:
    """When PK is row_hash (synthetic), use the natural key from row_hash_keys."""
    cols = _resolve_upsert_conflict_columns(
        "espn_teams",
        pk_cols=["row_hash"],
        csv_cols=["row_hash", "espn_id", "name", "abbreviation", "logo", "pulled_at_utc", "source", "parse_version"],
        table_cols=["row_hash", "espn_id", "name", "abbreviation", "logo", "pulled_at_utc", "source", "parse_version"],
    )
    assert cols == ["espn_id"]

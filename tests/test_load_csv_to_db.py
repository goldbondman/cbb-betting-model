import csv
from pathlib import Path

from load_csv_to_db import _missing_table_migration_path, _prepare_csv_for_load, _preflight_validate_csv


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


def test_missing_table_migration_path_for_espn_player_boxscores() -> None:
    migration = _missing_table_migration_path("raw", "espn_player_boxscores")
    assert migration is not None
    assert migration.name == "20260315000000_create_raw_espn_player_boxscores.sql"

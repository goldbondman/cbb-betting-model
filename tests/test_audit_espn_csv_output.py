import csv
import os
import sys
import tempfile
from pathlib import Path

import pytest

_SCRIPTS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"
)
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from audit_espn_csv_output import audit_csv, run_audit, EXPECTED_FILES, main


class TestAuditCsv:
    """Unit tests for the single-file audit function."""

    def test_missing_file_fails(self, tmp_path):
        spec = {"file": "does_not_exist.csv", "min_rows": 1, "level": "critical"}
        result = audit_csv(str(tmp_path), spec)
        assert result["passed"] is False
        assert result["exists"] is False
        assert "not found" in result["reason"]

    def test_empty_file_fails(self, tmp_path):
        p = tmp_path / "empty.csv"
        p.write_text("")
        spec = {"file": "empty.csv", "min_rows": 1, "level": "critical"}
        result = audit_csv(str(tmp_path), spec)
        assert result["passed"] is False
        assert result["exists"] is True
        assert "empty" in result["reason"]

    def test_header_only_with_min_rows_zero_passes(self, tmp_path):
        p = tmp_path / "header_only.csv"
        p.write_text("col_a,col_b\n")
        spec = {"file": "header_only.csv", "min_rows": 0, "level": "optional"}
        result = audit_csv(str(tmp_path), spec)
        assert result["passed"] is True
        assert result["header_ok"] is True
        assert result["row_count"] == 0

    def test_header_only_with_min_rows_one_fails(self, tmp_path):
        p = tmp_path / "header_only.csv"
        p.write_text("col_a,col_b\n")
        spec = {"file": "header_only.csv", "min_rows": 1, "level": "critical"}
        result = audit_csv(str(tmp_path), spec)
        assert result["passed"] is False
        assert result["row_count"] == 0

    def test_valid_csv_passes(self, tmp_path):
        p = tmp_path / "good.csv"
        p.write_text("col_a,col_b\n1,2\n3,4\n")
        spec = {"file": "good.csv", "min_rows": 1, "level": "critical"}
        result = audit_csv(str(tmp_path), spec)
        assert result["passed"] is True
        assert result["row_count"] == 2
        assert result["header_ok"] is True


class TestRunAudit:
    """Integration tests for the full audit run."""

    def test_all_critical_present_returns_pass(self, tmp_path):
        for spec in EXPECTED_FILES:
            p = tmp_path / spec["file"]
            p.write_text("a,b\n1,2\n")
        results = run_audit(str(tmp_path))
        critical_failures = [r for r in results if not r["passed"] and r["level"] == "critical"]
        assert len(critical_failures) == 0

    def test_missing_critical_file_detected(self, tmp_path):
        # Create all files except espn_games.csv
        for spec in EXPECTED_FILES:
            if spec["file"] != "espn_games.csv":
                p = tmp_path / spec["file"]
                p.write_text("a,b\n1,2\n")
        results = run_audit(str(tmp_path))
        games_result = next(r for r in results if r["file"] == "espn_games.csv")
        assert games_result["passed"] is False


class TestMainExitCode:
    """Test the main() entry point exit codes."""

    def test_exit_zero_when_all_critical_pass(self, tmp_path):
        for spec in EXPECTED_FILES:
            p = tmp_path / spec["file"]
            p.write_text("a,b\n1,2\n")
        code = main(["--csv-dir", str(tmp_path)])
        assert code == 0

    def test_exit_one_when_critical_missing(self, tmp_path):
        # Don't create any files
        code = main(["--csv-dir", str(tmp_path)])
        assert code == 1

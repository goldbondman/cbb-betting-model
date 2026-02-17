from pathlib import Path


def test_update_espn_workflow_uploads_csv_artifacts():
    workflow_path = (
        Path(__file__).resolve().parents[1]
        / ".github"
        / "workflows"
        / "update-espn-csvs.yml"
    )
    workflow_text = workflow_path.read_text(encoding="utf-8")

    assert "actions/upload-artifact@v4" in workflow_text
    assert "name: espn-csvs" in workflow_text
    assert "path: ESPN/CSV/*.csv" in workflow_text

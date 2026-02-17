from pathlib import Path

import yaml


def test_update_espn_workflow_uploads_csv_artifacts():
    workflow_path = (
        Path(__file__).resolve().parents[1]
        / ".github"
        / "workflows"
        / "update-espn-csvs.yml"
    )
    workflow = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
    steps = workflow["jobs"]["update"]["steps"]
    artifact_step = next(
        (step for step in steps if step.get("name") == "Upload ESPN CSV artifacts"),
        None,
    )
    assert artifact_step is not None

    assert artifact_step["uses"] == "actions/upload-artifact@v4"
    assert artifact_step["with"]["name"] == "espn-csvs"
    assert artifact_step["with"]["path"] == "ESPN/CSV/*.csv"

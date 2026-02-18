from pathlib import Path

import yaml


def _load_espn_workflow_steps():
    workflow_path = (
        Path(__file__).resolve().parents[1]
        / ".github"
        / "workflows"
        / "update-espn-csvs.yml"
    )
    workflow = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
    return workflow["jobs"]["update"]["steps"]


def test_update_espn_workflow_uploads_csv_artifacts():
    steps = _load_espn_workflow_steps()
    artifact_step = next(
        (step for step in steps if step.get("name") == "Upload ESPN CSV artifacts"),
        None,
    )
    assert artifact_step is not None

    assert artifact_step["uses"] == "actions/upload-artifact@v4"
    assert artifact_step["with"]["name"] == "espn-csvs"
    assert artifact_step["with"]["path"] == "ESPN/CSV/*.csv"


def test_update_espn_workflow_has_csv_audit_step():
    steps = _load_espn_workflow_steps()
    audit_step = next(
        (step for step in steps if step.get("name") == "Audit ESPN CSV output"),
        None,
    )
    assert audit_step is not None
    assert audit_step.get("id") == "csv_audit"


def test_update_espn_workflow_has_recovery_step():
    steps = _load_espn_workflow_steps()
    recovery_step = next(
        (step for step in steps if step.get("name") == "Recover missing CSVs from Supabase Storage"),
        None,
    )
    assert recovery_step is not None


def test_audit_runs_before_upload_artifacts():
    steps = _load_espn_workflow_steps()
    step_names = [s.get("name") for s in steps]
    audit_idx = step_names.index("Audit ESPN CSV output")
    upload_idx = step_names.index("Upload ESPN CSV artifacts")
    assert audit_idx < upload_idx

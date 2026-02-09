#!/usr/bin/env python3
"""
End-to-end ML pipeline: feature matrix -> train -> predict.

Goals:
- Run as a script (no package install required)
- Produce a deterministic run log (even on failure)
- Capture key artifact paths + light integrity metadata
"""

from __future__ import annotations

import os
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

# Allow running as a script without installing as a package.
ML_DIR = Path(__file__).resolve().parent
if str(ML_DIR) not in sys.path:
    sys.path.insert(0, str(ML_DIR))

from feature_matrix import build_feature_matrix, BuildConfig
from train_ml_models import train_models, TrainConfig
from predict_ml import predict, PredictConfig
from run_logger import write_run_log
from schema import feature_schema_hash


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _safe_get_file_hash(path: Path, algo: str = "sha256") -> Optional[str]:
    """
    Best-effort file hash for small artifacts (json/csv).
    Returns None if missing or unreadable.
    """
    try:
        if not path.exists() or not path.is_file():
            return None
        import hashlib

        h = hashlib.new(algo)
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return None


def run_pipeline() -> Dict[str, Any]:
    build_cfg = BuildConfig()
    train_cfg = TrainConfig()
    predict_cfg = PredictConfig()

    run_log_path = ML_DIR / "run_log.json"
    run_log_path.parent.mkdir(parents=True, exist_ok=True)

    # Try to infer intended artifact locations without importing internal vars from other modules
    features_out = getattr(build_cfg, "out_features_path", ML_DIR / "model_features.csv")
    schema_out = getattr(build_cfg, "out_schema_path", ML_DIR / "feature_schema_hash.txt")
    audit_out = getattr(build_cfg, "out_audit_path", ML_DIR / "dq_audit_ml.csv")

    margin_model_path = getattr(predict_cfg, "margin_model_path", ML_DIR / "models" / "margin_model.json")
    total_model_path = getattr(predict_cfg, "total_model_path", ML_DIR / "models" / "total_model.json")
    preds_out = getattr(predict_cfg, "out_path", ML_DIR / "predictions_latest.csv")

    run_log: Dict[str, Any] = {
        "status": "started",
        "started_at_utc": _utc_now_iso(),
        "feature_schema_hash": feature_schema_hash(),
        "model_version": getattr(train_cfg, "model_version", None),
        "env": {
            "ML_MODEL_VERSION": os.getenv("ML_MODEL_VERSION"),
            "ML_VAL_SPLIT": os.getenv("ML_VAL_SPLIT"),
            "ML_RIDGE_LAMBDA": os.getenv("ML_RIDGE_LAMBDA"),
        },
        "artifacts": {
            "model_features_csv": str(features_out),
            "dq_audit_ml_csv": str(audit_out),
            "feature_schema_hash_txt": str(schema_out),
            "margin_model_json": str(margin_model_path),
            "total_model_json": str(total_model_path),
            "predictions_latest_csv": str(preds_out),
        },
        "train_metrics": {},
        "val_metrics": {},
        "notes": [],
    }

    try:
        # Step 1: features
        build_feature_matrix(build_cfg)

        # Step 2: train
        train_results = train_models(train_cfg)

        # Step 3: predict
        predict(predict_cfg)

        # Extract metrics defensively
        train_metrics: Dict[str, Any] = {}
        val_metrics: Dict[str, Any] = {}
        if isinstance(train_results, dict):
            for k, v in train_results.items():
                if isinstance(v, dict):
                    train_metrics[k] = v.get("rmse")
                    val_metrics[k] = v.get("val_rmse")
                else:
                    train_metrics[k] = None
                    val_metrics[k] = None
        else:
            run_log["notes"].append("train_results_not_dict")

        # Best-effort artifact hashes for drift detection
        run_log["artifact_hashes"] = {
            "model_features_csv": _safe_get_file_hash(Path(str(features_out))),
            "margin_model_json": _safe_get_file_hash(Path(str(margin_model_path))),
            "total_model_json": _safe_get_file_hash(Path(str(total_model_path))),
            "predictions_latest_csv": _safe_get_file_hash(Path(str(preds_out))),
        }

        run_log.update(
            {
                "status": "succeeded",
                "finished_at_utc": _utc_now_iso(),
                "train_metrics": train_metrics,
                "val_metrics": val_metrics,
            }
        )
        return run_log

    except Exception as e:
        run_log.update(
            {
                "status": "failed",
                "finished_at_utc": _utc_now_iso(),
                "error": repr(e),
                "traceback": traceback.format_exc(limit=50),
            }
        )
        raise

    finally:
        # Always emit the run log, even if upstream raises
        write_run_log(run_log_path, run_log)


def main() -> None:
    run_pipeline()


if __name__ == "__main__":
    main()

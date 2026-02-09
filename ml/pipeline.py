#!/usr/bin/env python3
"""
End-to-end ML pipeline: feature matrix -> train -> predict.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict

# Allow running as a script without installing as a package.
ML_DIR = Path(__file__).resolve().parent
if str(ML_DIR) not in sys.path:
    sys.path.insert(0, str(ML_DIR))

from feature_matrix import build_feature_matrix, BuildConfig
from train_ml_models import train_models, TrainConfig
from predict_ml import predict, PredictConfig
from run_logger import write_run_log
from schema import feature_schema_hash


def run_pipeline() -> Dict[str, Any]:
    build_cfg = BuildConfig()
    train_cfg = TrainConfig()
    predict_cfg = PredictConfig()

    run_log_path = ML_DIR / "run_log.json"
    run_log_path.parent.mkdir(parents=True, exist_ok=True)

    run_log: Dict[str, Any] = {
        "status": "started",
        "feature_schema_hash": feature_schema_hash(),
        "model_version": getattr(train_cfg, "model_version", None),
        "train_rows": None,
        "train_metrics": {},
        "val_metrics": {},
    }

    try:
        build_feature_matrix(build_cfg)
        train_results = train_models(train_cfg)
        predict(predict_cfg)

        # Defensive extraction
        train_metrics = {}
        val_metrics = {}
        if isinstance(train_results, dict):
            for k, v in train_results.items():
                if isinstance(v, dict):
                    train_metrics[k] = v.get("rmse")
                    val_metrics[k] = v.get("val_rmse")
                else:
                    train_metrics[k] = None
                    val_metrics[k] = None

        run_log.update(
            {
                "status": "succeeded",
                "train_metrics": train_metrics,
                "val_metrics": val_metrics,
            }
        )
        return run_log

    except Exception as e:
        run_log.update({"status": "failed", "error": repr(e)})
        raise

    finally:
        write_run_log(run_log_path, run_log)


def main() -> None:
    run_pipeline()


if __name__ == "__main__":
    main()

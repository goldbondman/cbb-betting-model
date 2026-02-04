#!/usr/bin/env python3
"""
End-to-end ML pipeline: feature matrix -> train -> predict.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Dict

from pathlib import Path as _Path
_ML_DIR = _Path(__file__).resolve().parent
if str(_ML_DIR) not in sys.path:
    sys.path.insert(0, str(_ML_DIR))

from feature_matrix import build_feature_matrix, BuildConfig
from train_ml_models import train_models, TrainConfig
from predict_ml import predict, PredictConfig
from run_logger import write_run_log
from schema import feature_schema_hash


def run_pipeline() -> Dict[str, object]:
    build_cfg = BuildConfig()
    train_cfg = TrainConfig()
    predict_cfg = PredictConfig()

    build_feature_matrix(build_cfg)
    train_results = train_models(train_cfg)
    predict(predict_cfg)

    run_log = {
        "feature_schema_hash": feature_schema_hash(),
        "model_version": train_cfg.model_version,
        "train_rows": None,
        "train_metrics": {k: v.get("rmse") for k, v in train_results.items()},
        "val_metrics": {k: v.get("val_rmse") for k, v in train_results.items()},
    }
    write_run_log(Path("ml/run_log.json"), run_log)
    return run_log


def main() -> None:
    run_pipeline()


if __name__ == "__main__":
    main()

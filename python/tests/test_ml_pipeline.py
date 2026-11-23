"""Tests for leakage-free ML training, evaluation metrics, and permutation sanity."""

from __future__ import annotations

import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import pytest
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import accuracy_score, f1_score, roc_auc_score

from plc500_ml.config import (
    ANOMALY_MODEL_PATH,
    EVALUATION_REPORT_PATH,
    MODEL_METADATA_PATH,
    PROCESSED_FEATURES_PATH,
    SEED,
    SEVERITY_MODEL_PATH,
)
from plc500_ml.features import MODEL_FEATURE_COLUMNS
from plc500_ml.train import train_and_evaluate, verify_no_leakage


def test_committed_model_artifacts_load_and_meet_quality_bars() -> None:
    for path in (
        ANOMALY_MODEL_PATH,
        SEVERITY_MODEL_PATH,
        MODEL_METADATA_PATH,
        EVALUATION_REPORT_PATH,
    ):
        assert path.is_file(), f"Missing model artifact: {path}"
        assert path.stat().st_size > 0

    anomaly_model = joblib.load(ANOMALY_MODEL_PATH)
    severity_model = joblib.load(SEVERITY_MODEL_PATH)
    metadata = json.loads(MODEL_METADATA_PATH.read_text(encoding="utf-8"))

    assert metadata["feature_names"] == list(MODEL_FEATURE_COLUMNS)
    assert metadata["leakage_verification"]["leakage_free"] is True

    df = pd.read_csv(PROCESSED_FEATURES_PATH)
    train_df = df[df["split"] == "train"]
    test_df = df[df["split"] == "test"]

    # Normal-only fit invariant
    assert metadata["anomaly_detector"]["normal_train_count"] == int(
        (train_df["severity"] == 0).sum()
    )

    X_test = test_df[list(MODEL_FEATURE_COLUMNS)].to_numpy(dtype=float)
    y_test = test_df["severity"].to_numpy(dtype=int)

    # Unsupervised anomaly detector ROC-AUC >= 0.85
    y_bin = (y_test > 0).astype(int)
    anom_scores = -anomaly_model.decision_function(X_test)
    assert roc_auc_score(y_bin, anom_scores) >= 0.85

    # Supervised severity classifier generalization
    y_pred = severity_model.predict(X_test)
    assert accuracy_score(y_test, y_pred) >= 0.90
    assert f1_score(y_test, y_pred, average="macro") >= 0.85
    per_class_f1 = f1_score(y_test, y_pred, average=None, labels=[0, 1, 2, 3])
    assert min(per_class_f1) >= 0.75


def test_verify_no_leakage_catches_synthetic_violations() -> None:
    df = pd.read_csv(PROCESSED_FEATURES_PATH)
    audit = verify_no_leakage(df, MODEL_FEATURE_COLUMNS)
    assert audit["leakage_free"] is True

    # Forbidden column in feature list
    with pytest.raises(ValueError, match="forbidden"):
        verify_no_leakage(df, (*MODEL_FEATURE_COLUMNS, "estado_clp"))

    # Purge gap < 60s
    bad_gap = df.copy()
    bad_gap.loc[bad_gap["split"] == "val", "window_start_s"] -= 10.0
    with pytest.raises(ValueError, match="Temporal leakage"):
        verify_no_leakage(bad_gap, MODEL_FEATURE_COLUMNS)

    # Raw sample overlap
    bad_raw = df.copy()
    bad_raw.loc[bad_raw["split"] == "val", "raw_start_idx"] -= 150
    with pytest.raises(ValueError, match="overlap"):
        verify_no_leakage(bad_raw, MODEL_FEATURE_COLUMNS)


def test_label_permutation_sanity_check() -> None:
    df = pd.read_csv(PROCESSED_FEATURES_PATH)
    train_df = df[df["split"] == "train"]
    test_df = df[df["split"] == "test"]

    X_train = train_df[list(MODEL_FEATURE_COLUMNS)].to_numpy(dtype=float)
    y_train_shuffled = np.random.default_rng(SEED).permutation(
        train_df["severity"].to_numpy(dtype=int)
    )
    X_test = test_df[list(MODEL_FEATURE_COLUMNS)].to_numpy(dtype=float)
    y_test = test_df["severity"].to_numpy(dtype=int)

    clf = HistGradientBoostingClassifier(max_iter=50, random_state=SEED)
    clf.fit(X_train, y_train_shuffled)
    shuffled_macro_f1 = f1_score(y_test, clf.predict(X_test), average="macro")
    assert shuffled_macro_f1 < 0.45


def test_isolated_training_smoke_in_tmp_dir(tmp_path: Path) -> None:
    meta = train_and_evaluate(
        features_path=PROCESSED_FEATURES_PATH,
        models_dir=tmp_path,
        seed=SEED,
    )
    assert (tmp_path / "anomaly_detector.joblib").is_file()
    assert (tmp_path / "severity_classifier.joblib").is_file()
    assert (tmp_path / "model_metadata.json").is_file()
    assert (tmp_path / "evaluation_report.md").is_file()
    assert meta["severity_classifier"]["selected_model"] in {
        "HistGradientBoostingClassifier",
        "RandomForestClassifier",
    }

"""Leakage-verified ML training & evaluation pipeline for WEG PLC500."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
import sklearn
from sklearn.ensemble import HistGradientBoostingClassifier, IsolationForest, RandomForestClassifier
from sklearn.inspection import permutation_importance
from sklearn.metrics import (
    accuracy_score,
    average_precision_score,
    confusion_matrix,
    f1_score,
    log_loss,
    precision_recall_fscore_support,
    precision_score,
    recall_score,
    roc_auc_score,
)
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from plc500_ml.config import (
    BASELINE_EPS,
    BASELINE_MU,
    BASELINE_SIGMA,
    MODELS_DIR,
    PROCESSED_FEATURES_PATH,
    PURGE_GAP_S,
    SEED,
    SEVERITY_LABELS,
    W,
)
from plc500_ml.features import FORBIDDEN_LEAKAGE_COLUMNS, MODEL_FEATURE_COLUMNS


def verify_no_leakage(
    df: pd.DataFrame,
    feature_columns: Sequence[str] = MODEL_FEATURE_COLUMNS,
) -> dict[str, Any]:
    """Verify feature non-leakage, >= 60s temporal purge gaps, and zero raw sample overlap."""
    forbidden_overlap = set(feature_columns).intersection(FORBIDDEN_LEAKAGE_COLUMNS)
    if forbidden_overlap:
        bad_cols = sorted(forbidden_overlap)
        raise ValueError(
            f"Leakage violation: forbidden columns present in feature_columns: {bad_cols}"
        )

    missing_cols = [c for c in feature_columns if c not in df.columns]
    if missing_cols:
        raise ValueError(f"Missing required feature columns: {missing_cols}")

    if "split" not in df.columns:
        raise ValueError("Dataset must contain 'split' column.")

    splits_present = set(df["split"].unique())
    required_splits = {"train", "val", "test"}
    if not required_splits.issubset(splits_present):
        raise ValueError(f"Dataset missing splits: {sorted(required_splits - splits_present)}")

    train_df = df[df["split"] == "train"]
    val_df = df[df["split"] == "val"]
    test_df = df[df["split"] == "test"]

    if train_df.empty or val_df.empty or test_df.empty:
        raise ValueError("All splits (train, val, test) must be non-empty.")

    train_val_gap_s = float(val_df["window_start_s"].min() - train_df["window_end_s"].max())
    val_test_gap_s = float(test_df["window_start_s"].min() - val_df["window_end_s"].max())

    if train_val_gap_s < PURGE_GAP_S:
        raise ValueError(
            f"Temporal leakage: train->val gap {train_val_gap_s:.2f}s < {PURGE_GAP_S:.2f}s"
        )
    if val_test_gap_s < PURGE_GAP_S:
        raise ValueError(
            f"Temporal leakage: val->test gap {val_test_gap_s:.2f}s < {PURGE_GAP_S:.2f}s"
        )

    overlapping_raw = 0
    if {"raw_start_idx", "raw_end_idx"}.issubset(df.columns):
        if int(train_df["raw_end_idx"].max()) >= int(val_df["raw_start_idx"].min()):
            raise ValueError("Raw sample overlap detected between train and val splits.")
        if int(val_df["raw_end_idx"].max()) >= int(test_df["raw_start_idx"].min()):
            raise ValueError("Raw sample overlap detected between val and test splits.")

        train_idx = set()
        for s, e in zip(train_df["raw_start_idx"], train_df["raw_end_idx"], strict=True):
            train_idx.update(range(int(s), int(e) + 1))
        val_idx = set()
        for s, e in zip(val_df["raw_start_idx"], val_df["raw_end_idx"], strict=True):
            val_idx.update(range(int(s), int(e) + 1))
        test_idx = set()
        for s, e in zip(test_df["raw_start_idx"], test_df["raw_end_idx"], strict=True):
            test_idx.update(range(int(s), int(e) + 1))

        overlapping_raw = (
            len(train_idx & val_idx) + len(val_idx & test_idx) + len(train_idx & test_idx)
        )
        if overlapping_raw > 0:
            raise ValueError(f"Detected {overlapping_raw} overlapping raw sample indices.")

    return {
        "feature_count": len(feature_columns),
        "forbidden_overlap_count": 0,
        "train_val_gap_s": train_val_gap_s,
        "val_test_gap_s": val_test_gap_s,
        "overlapping_raw_samples": overlapping_raw,
        "leakage_free": True,
    }


def _evaluate_anomaly_detector(
    model: Pipeline,
    X: np.ndarray,
    y_severity: np.ndarray,
) -> dict[str, float]:
    y_bin = (y_severity > 0).astype(int)
    scores = -model.decision_function(X)
    preds = (model.predict(X) == -1).astype(int)

    roc_auc = float(roc_auc_score(y_bin, scores)) if len(np.unique(y_bin)) > 1 else 0.0
    pr_auc = float(average_precision_score(y_bin, scores)) if len(np.unique(y_bin)) > 1 else 0.0
    return {
        "roc_auc": round(roc_auc, 4),
        "pr_auc": round(pr_auc, 4),
        "f1": round(float(f1_score(y_bin, preds, zero_division=0)), 4),
        "precision": round(float(precision_score(y_bin, preds, zero_division=0)), 4),
        "recall": round(float(recall_score(y_bin, preds, zero_division=0)), 4),
        "anomaly_rate_pred": round(float(np.mean(preds)), 4),
        "anomaly_rate_true": round(float(np.mean(y_bin)), 4),
    }


def _evaluate_classifier(
    model: Any,
    X: np.ndarray,
    y_true: np.ndarray,
) -> dict[str, Any]:
    y_pred = model.predict(X)
    y_proba = model.predict_proba(X)

    acc = float(accuracy_score(y_true, y_pred))
    macro_f1 = float(f1_score(y_true, y_pred, average="macro", zero_division=0))
    weighted_f1 = float(f1_score(y_true, y_pred, average="weighted", zero_division=0))
    ll = float(log_loss(y_true, y_proba, labels=[0, 1, 2, 3]))
    try:
        roc_auc_ovr = float(
            roc_auc_score(y_true, y_proba, multi_class="ovr", average="macro", labels=[0, 1, 2, 3])
        )
    except ValueError:
        roc_auc_ovr = 0.0

    prec, rec, f1, sup = precision_recall_fscore_support(
        y_true, y_pred, labels=[0, 1, 2, 3], zero_division=0
    )
    per_class: dict[str, dict[str, Any]] = {}
    for idx, label in enumerate(SEVERITY_LABELS):
        per_class[label] = {
            "precision": round(float(prec[idx]), 4),
            "recall": round(float(rec[idx]), 4),
            "f1": round(float(f1[idx]), 4),
            "support": int(sup[idx]),
        }

    cm = confusion_matrix(y_true, y_pred, labels=[0, 1, 2, 3]).tolist()

    return {
        "accuracy": round(acc, 4),
        "macro_f1": round(macro_f1, 4),
        "weighted_f1": round(weighted_f1, 4),
        "log_loss": round(ll, 4),
        "roc_auc_ovr": round(roc_auc_ovr, 4),
        "per_class": per_class,
        "confusion_matrix": cm,
    }


def _build_evaluation_report(metadata: dict[str, Any]) -> str:
    leak = metadata["leakage_verification"]
    anom = metadata["anomaly_detector"]
    clf = metadata["severity_classifier"]
    winner_name = clf["selected_model"]
    winner_test = clf["candidates"][winner_name]["test"]
    hgb_val = clf["candidates"]["HistGradientBoostingClassifier"]["val"]
    rf_val = clf["candidates"]["RandomForestClassifier"]["val"]
    hgb_test = clf["candidates"]["HistGradientBoostingClassifier"]["test"]
    rf_test = clf["candidates"]["RandomForestClassifier"]["test"]

    cm_rows = "\n".join(
        f"| `{SEVERITY_LABELS[i]}` | " + " | ".join(str(x) for x in row) + " |"
        for i, row in enumerate(winner_test["confusion_matrix"])
    )
    per_class_rows = "\n".join(
        f"| `{lbl}` | {m['precision']:.4f} | {m['recall']:.4f} | {m['f1']:.4f} | {m['support']} |"
        for lbl, m in winner_test["per_class"].items()
    )
    feat_rows = "\n".join(
        f"| `{item['feature']}` | {item['importance_mean']:.4f} | {item['importance_std']:.4f} |"
        for item in clf["feature_importances"]
    )
    av, at = anom["val"], anom["test"]
    anom_val_row = (
        f"| `val` | {av['roc_auc']:.4f} | {av['pr_auc']:.4f} | "
        f"{av['f1']:.4f} | {av['precision']:.4f} | {av['recall']:.4f} |"
    )
    anom_test_row = (
        f"| `test` | {at['roc_auc']:.4f} | {at['pr_auc']:.4f} | "
        f"{at['f1']:.4f} | {at['precision']:.4f} | {at['recall']:.4f} |"
    )
    clf_hdr = (
        "| Model | Val Accuracy | Val Macro-F1 | Test Accuracy | "
        "Test Macro-F1 | Test Weighted-F1 | Test ROC-AUC (OvR) |"
    )
    hgb_row = (
        f"| `HistGradientBoostingClassifier` | {hgb_val['accuracy']:.4f} | "
        f"{hgb_val['macro_f1']:.4f} | {hgb_test['accuracy']:.4f} | "
        f"{hgb_test['macro_f1']:.4f} | {hgb_test['weighted_f1']:.4f} | "
        f"{hgb_test['roc_auc_ovr']:.4f} |"
    )
    rf_row = (
        f"| `RandomForestClassifier` | {rf_val['accuracy']:.4f} | "
        f"{rf_val['macro_f1']:.4f} | {rf_test['accuracy']:.4f} | "
        f"{rf_test['macro_f1']:.4f} | {rf_test['weighted_f1']:.4f} | "
        f"{rf_test['roc_auc_ovr']:.4f} |"
    )
    norm_cnt = anom["normal_train_count"]
    n_feats = len(metadata["feature_names"])

    return f"""# Model Evaluation Report — WEG PLC500 ML Pipeline

- **Model Version**: `{metadata["model_version"]}`
- **Trained At (UTC)**: `{metadata["trained_at_utc"]}`
- **Random Seed**: `{metadata["seed"]}`
- **Feature Count**: `{n_feats}` (`MODEL_FEATURE_COLUMNS`, zero train/serve skew)

## 1. Temporal Split & Leakage Audit

| Check | Result | Value |
|---|---|---|
| Forbidden Columns in `X` | PASS | `{leak["forbidden_overlap_count"]}` |
| `train -> val` Purge Gap | PASS | `{leak["train_val_gap_s"]:.1f} s` (`>= {PURGE_GAP_S:.1f} s`) |
| `val -> test` Purge Gap | PASS | `{leak["val_test_gap_s"]:.1f} s` (`>= {PURGE_GAP_S:.1f} s`) |
| Overlapping Raw Samples | PASS | `{leak["overlapping_raw_samples"]}` |
| Normal Train Windows (IsolationForest Fit) | PASS | `{norm_cnt}` (`severity == 0` only) |

## 2. Unsupervised Anomaly Detector (`StandardScaler + IsolationForest`)

| Split | ROC-AUC | PR-AUC | F1 | Precision | Recall |
|---|---:|---:|---:|---:|---:|
{anom_val_row}
{anom_test_row}

## 3. Severity Classifier Comparison (`val` Selection -> `test` Evaluation)

- **Selected Winner**: `{winner_name}`

{clf_hdr}
|---|---:|---:|---:|---:|---:|---:|
{hgb_row}
{rf_row}

## 4. Winner Per-Class Test Metrics (`{winner_name}`)

| Class | Precision | Recall | F1 | Support |
|---|---:|---:|---:|---:|
{per_class_rows}

## 5. Winner 4x4 Test Confusion Matrix (Rows=True, Cols=Pred)

| True \\ Pred | NORMAL | ATENCAO | CRITICO | EMERGENCIA |
|---|---:|---:|---:|---:|
{cm_rows}

## 6. Top Permutation Feature Importances (Validation Split)

| Feature | Importance Mean | Std |
|---|---:|---:|
{feat_rows}
"""


def train_and_evaluate(
    *,
    features_path: Path | str | None = None,
    models_dir: Path | str | None = None,
    seed: int = SEED,
) -> dict[str, Any]:
    """Train anomaly detector & severity classifier with strict leakage audit."""
    feat_csv = Path(features_path) if features_path is not None else PROCESSED_FEATURES_PATH
    out_dir = Path(models_dir) if models_dir is not None else MODELS_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    df = pd.read_csv(feat_csv)
    leakage_audit = verify_no_leakage(df, MODEL_FEATURE_COLUMNS)

    train_df = df[df["split"] == "train"].reset_index(drop=True)
    val_df = df[df["split"] == "val"].reset_index(drop=True)
    test_df = df[df["split"] == "test"].reset_index(drop=True)

    feat_cols = list(MODEL_FEATURE_COLUMNS)
    X_train = train_df[feat_cols].to_numpy(dtype=float)
    y_train = train_df["severity"].to_numpy(dtype=int)
    X_val = val_df[feat_cols].to_numpy(dtype=float)
    y_val = val_df["severity"].to_numpy(dtype=int)
    X_test = test_df[feat_cols].to_numpy(dtype=float)
    y_test = test_df["severity"].to_numpy(dtype=int)

    # 1. Unsupervised Anomaly Detector: fit strictly on NORMAL training windows
    train_normal_df = train_df[train_df["severity"] == 0]
    X_train_normal = train_normal_df[feat_cols].to_numpy(dtype=float)

    anomaly_pipeline = Pipeline(
        [
            ("scaler", StandardScaler()),
            (
                "isolation_forest",
                IsolationForest(
                    n_estimators=200,
                    contamination=0.02,
                    max_samples="auto",
                    random_state=seed,
                    n_jobs=1,
                ),
            ),
        ]
    )
    anomaly_pipeline.fit(X_train_normal)
    anom_val_metrics = _evaluate_anomaly_detector(anomaly_pipeline, X_val, y_val)
    anom_test_metrics = _evaluate_anomaly_detector(anomaly_pipeline, X_test, y_test)

    # 2. Supervised Severity Classifier Benchmark: HGB vs RF on val macro_f1
    best_hgb = None
    best_hgb_params: dict[str, Any] = {}
    best_hgb_val: dict[str, Any] = {}
    best_hgb_key = (-1.0, -1.0, -999.0)

    for max_iter in (100, 200):
        for lr in (0.05, 0.1):
            for max_depth in (4, 6, None):
                clf = HistGradientBoostingClassifier(
                    max_iter=max_iter,
                    learning_rate=lr,
                    max_depth=max_depth,
                    random_state=seed,
                )
                clf.fit(X_train, y_train)
                val_m = _evaluate_classifier(clf, X_val, y_val)
                key = (val_m["macro_f1"], val_m["accuracy"], -val_m["log_loss"])
                if key > best_hgb_key:
                    best_hgb_key = key
                    best_hgb = clf
                    best_hgb_params = {
                        "max_iter": max_iter,
                        "learning_rate": lr,
                        "max_depth": max_depth,
                    }
                    best_hgb_val = val_m

    best_rf = None
    best_rf_params: dict[str, Any] = {}
    best_rf_val: dict[str, Any] = {}
    best_rf_key = (-1.0, -1.0, -999.0)

    for n_est in (100, 200):
        for max_depth in (8, 14, None):
            for cw in (None, "balanced"):
                clf = RandomForestClassifier(
                    n_estimators=n_est,
                    max_depth=max_depth,
                    class_weight=cw,
                    random_state=seed,
                    n_jobs=1,
                )
                clf.fit(X_train, y_train)
                val_m = _evaluate_classifier(clf, X_val, y_val)
                key = (val_m["macro_f1"], val_m["accuracy"], -val_m["log_loss"])
                if key > best_rf_key:
                    best_rf_key = key
                    best_rf = clf
                    best_rf_params = {
                        "n_estimators": n_est,
                        "max_depth": max_depth,
                        "class_weight": cw,
                    }
                    best_rf_val = val_m

    assert best_hgb is not None and best_rf is not None

    if best_hgb_val["macro_f1"] >= best_rf_val["macro_f1"] - 1e-4:
        selected_name = "HistGradientBoostingClassifier"
        selected_model = best_hgb
    else:
        selected_name = "RandomForestClassifier"
        selected_model = best_rf

    hgb_test_metrics = _evaluate_classifier(best_hgb, X_test, y_test)
    rf_test_metrics = _evaluate_classifier(best_rf, X_test, y_test)

    perm = permutation_importance(
        selected_model,
        X_val,
        y_val,
        n_repeats=10,
        random_state=seed,
        scoring="f1_macro",
        n_jobs=1,
    )
    feat_importances = sorted(
        [
            {
                "feature": feat_cols[i],
                "importance_mean": round(float(perm.importances_mean[i]), 4),
                "importance_std": round(float(perm.importances_std[i]), 4),
            }
            for i in range(len(feat_cols))
        ],
        key=lambda d: d["importance_mean"],
        reverse=True,
    )

    model_version = "1.0.0"
    metadata: dict[str, Any] = {
        "model_version": model_version,
        "trained_at_utc": datetime.now(tz=UTC).isoformat().replace("+00:00", "Z"),
        "seed": seed,
        "window_seconds": W,
        "baseline": {
            "mu": BASELINE_MU,
            "sigma": BASELINE_SIGMA,
            "eps": BASELINE_EPS,
        },
        "feature_names": feat_cols,
        "feature_columns": feat_cols,
        "class_labels": list(SEVERITY_LABELS),
        "split_counts": {
            "train": int(len(train_df)),
            "val": int(len(val_df)),
            "test": int(len(test_df)),
        },
        "leakage_verification": leakage_audit,
        "anomaly_detector": {
            "algorithm": "StandardScaler + IsolationForest",
            "normal_train_count": int(len(train_normal_df)),
            "params": {"n_estimators": 200, "contamination": 0.02, "random_state": seed},
            "val": anom_val_metrics,
            "test": anom_test_metrics,
        },
        "severity_classifier": {
            "selection_metric": "val_macro_f1",
            "selected_model": selected_name,
            "candidates": {
                "HistGradientBoostingClassifier": {
                    "params": best_hgb_params,
                    "val": best_hgb_val,
                    "test": hgb_test_metrics,
                },
                "RandomForestClassifier": {
                    "params": best_rf_params,
                    "val": best_rf_val,
                    "test": rf_test_metrics,
                },
            },
            "feature_importances": feat_importances,
        },
        "library_versions": {
            "scikit_learn": sklearn.__version__,
            "numpy": np.__version__,
            "pandas": pd.__version__,
            "joblib": joblib.__version__,
        },
    }

    joblib.dump(anomaly_pipeline, out_dir / "anomaly_detector.joblib", compress=3)
    joblib.dump(selected_model, out_dir / "severity_classifier.joblib", compress=3)
    (out_dir / "model_metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    (out_dir / "evaluation_report.md").write_text(
        _build_evaluation_report(metadata), encoding="utf-8"
    )
    return metadata


def main() -> None:
    parser = argparse.ArgumentParser(description="Train WEG PLC500 anomaly and severity models.")
    parser.add_argument(
        "--features-path",
        type=Path,
        default=PROCESSED_FEATURES_PATH,
        help="Path to window_features_60s.csv.",
    )
    parser.add_argument(
        "--models-dir",
        type=Path,
        default=MODELS_DIR,
        help="Output directory for trained model artifacts.",
    )
    parser.add_argument("--seed", type=int, default=SEED, help="PRNG seed.")
    args = parser.parse_args()

    meta = train_and_evaluate(
        features_path=args.features_path,
        models_dir=args.models_dir,
        seed=args.seed,
    )
    print(json.dumps(meta, indent=2))


if __name__ == "__main__":
    main()

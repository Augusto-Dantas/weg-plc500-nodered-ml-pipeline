"""Integrity, wrap-16, and zero-temporal-leakage tests for the 18h synthetic dataset."""

from __future__ import annotations

import json

import pandas as pd
import pytest

from plc500_ml.config import (
    PROCESSED_FEATURES_PATH,
    PURGE_GAP_S,
    RAW_DATA_PATH,
    RAW_SAMPLE_PATH,
    SCENARIO_SUMMARY_PATH,
    SEED,
    W,
)
from plc500_ml.data_generator import ALL_SCENARIOS, simulate_raw_telemetry
from plc500_ml.features import MODEL_FEATURE_COLUMNS, extract_features_from_raw_window


def test_data_deliverables_exist_and_under_github_limit() -> None:
    data_dir = RAW_DATA_PATH.parent.parent
    expected = [
        RAW_DATA_PATH,
        RAW_SAMPLE_PATH,
        PROCESSED_FEATURES_PATH,
        SCENARIO_SUMMARY_PATH,
        data_dir / "data_dictionary.md",
        data_dir / "README.md",
    ]
    for path in expected:
        assert path.is_file(), f"Missing data deliverable: {path}"
        assert path.stat().st_size > 0
        assert path.stat().st_size < 10 * 1024 * 1024, f"File exceeds 10 MB: {path}"


def test_raw_telemetry_integrity_and_wrap_around() -> None:
    raw_df = pd.read_csv(RAW_DATA_PATH)
    sample_df = pd.read_csv(RAW_SAMPLE_PATH)

    assert len(raw_df) == 129_600
    assert len(sample_df) == 500
    assert raw_df["time_s"].diff().dropna().eq(0.5).all()

    for col in ("cnt_presenca", "cnt_nivel_alto", "cnt_sobretemp", "cnt_emergencia"):
        assert (raw_df[col] >= 0).all() and (raw_df[col] <= 65535).all()

    assert raw_df["estado_clp"].isin([0, 1, 2, 3, 4]).all()
    assert raw_df["cod_intertrav"].isin([0, 1, 2, 3]).all()
    assert raw_df["mascara_do_real"].between(0, 15).all()

    # Verify genuine 65535 -> 0 wrap-around occurs in raw telemetry
    wraps = int((raw_df["cnt_presenca"].diff().dropna() < 0).sum())
    assert wraps >= 1


def test_strict_temporal_split_purge_gap_and_zero_raw_overlap() -> None:
    win_df = pd.read_csv(PROCESSED_FEATURES_PATH)
    assert len(win_df) >= 3600

    train_df = win_df[win_df["split"] == "train"]
    val_df = win_df[win_df["split"] == "val"]
    test_df = win_df[win_df["split"] == "test"]

    total = len(win_df)
    assert 0.63 <= len(train_df) / total <= 0.67
    assert 0.13 <= len(val_df) / total <= 0.17
    assert 0.18 <= len(test_df) / total <= 0.22

    assert float(train_df["window_end_s"].max()) + PURGE_GAP_S <= float(
        val_df["window_start_s"].min()
    )
    assert float(val_df["window_end_s"].max()) + PURGE_GAP_S <= float(
        test_df["window_start_s"].min()
    )

    # Disjoint raw sample index sets across all splits
    train_idx = {
        i
        for s, e in zip(train_df["raw_start_idx"], train_df["raw_end_idx"], strict=True)
        for i in range(int(s), int(e) + 1)
    }
    val_idx = {
        i
        for s, e in zip(val_df["raw_start_idx"], val_df["raw_end_idx"], strict=True)
        for i in range(int(s), int(e) + 1)
    }
    test_idx = {
        i
        for s, e in zip(test_df["raw_start_idx"], test_df["raw_end_idx"], strict=True)
        for i in range(int(s), int(e) + 1)
    }
    assert train_idx.isdisjoint(val_idx)
    assert val_idx.isdisjoint(test_idx)
    assert train_idx.isdisjoint(test_idx)


def test_all_scenarios_and_severities_in_every_split_and_feature_parity() -> None:
    win_df = pd.read_csv(PROCESSED_FEATURES_PATH)
    raw_df = pd.read_csv(RAW_DATA_PATH)
    summary = json.loads(SCENARIO_SUMMARY_PATH.read_text(encoding="utf-8"))

    assert summary["temporal_purge_audit"]["leakage_free"] is True
    assert summary["total_wrap_arounds"] >= 1

    for split_name in ("train", "val", "test"):
        sub = win_df[win_df["split"] == split_name]
        assert set(sub["severity"].unique()) == {0, 1, 2, 3}
        assert set(ALL_SCENARIOS).issubset(set(sub["scenario"].unique()))

    # Recompute features on sample windows (including wrap-around window) and verify exact parity
    wrap_rows = raw_df.index[raw_df["cnt_presenca"].diff().fillna(0) < 0].tolist()
    assert len(wrap_rows) >= 1
    wrap_idx = int(wrap_rows[0])
    target_windows = win_df[
        (win_df["raw_start_idx"] <= wrap_idx) & (win_df["raw_end_idx"] >= wrap_idx)
    ].head(1)
    check_indices = [0, 500, int(target_windows.index[0])] if not target_windows.empty else [0, 500]

    for w_idx in check_indices:
        row = win_df.iloc[w_idx]
        s_idx = int(row["raw_start_idx"])
        e_idx = int(row["raw_end_idx"]) + 1
        wf = extract_features_from_raw_window(raw_df.iloc[s_idx:e_idx].to_dict(orient="records"))
        assert wf.window_seconds == pytest.approx(W)
        for col in MODEL_FEATURE_COLUMNS:
            val = getattr(wf, col)
            expected = float(int(val) if isinstance(val, bool) else val)
            assert float(row[col]) == pytest.approx(expected, abs=1e-6)
        assert wf.delta_cnt_presenca >= 0


def test_seed_42_determinism() -> None:
    df1 = simulate_raw_telemetry(seed=SEED, duration_hours=0.2)
    df2 = simulate_raw_telemetry(seed=SEED, duration_hours=0.2)
    pd.testing.assert_frame_equal(df1, df2)

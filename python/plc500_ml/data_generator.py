"""Reproducible 18h @ 2Hz synthetic telemetry generator for WEG PLC500."""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from plc500_ml.config import (
    BASELINE_EPS,
    BASELINE_MU,
    BASELINE_SIGMA,
    DATA_DIR,
    DATASET_HOURS,
    ESTADO_ALERTA,
    ESTADO_EMERGENCIA_TRAV,
    ESTADO_MODO_DEGRADADO,
    ESTADO_NORMAL_AUTO,
    INTERTRAV_EMERGENCIA,
    INTERTRAV_NENHUM,
    INTERTRAV_SOBRETEMP,
    INTERTRAV_WATCHDOG,
    PROCESSED_FEATURES_PATH,
    PURGE_GAP_S,
    RAW_DATA_PATH,
    RAW_SAMPLE_PATH,
    SAMPLE_RATE_HZ,
    SCENARIO_SUMMARY_PATH,
    SEED,
    SEVERITY_CODE_TO_NAME,
    STRIDE_SAMPLES,
    TP,
    UINT16_MOD,
    K,
    W,
)
from plc500_ml.features import (
    AUDIT_FEATURE_COLUMNS,
    MODEL_FEATURE_COLUMNS,
    extract_features_from_raw_window,
)
from plc500_ml.safety_rules import evaluate_statistical_fallback

FAULT_SCENARIOS: tuple[str, ...] = (
    "cadence_drift",
    "chattering_b1",
    "conveyor_blockage",
    "intermittent_high_level",
    "sustained_high_level",
    "overtemperature",
    "watchdog_timeout",
    "emergency_stop",
    "multi_fault",
)

ALL_SCENARIOS: tuple[str, ...] = ("nominal",) + FAULT_SCENARIOS

# Strict temporal split boundaries on [0.0, 64800.0) aligned to 15.0 s / 0.5 s grid
TRAIN_END_S: float = 42120.0  # 13 * 3240 s (11.7 h)
PURGE1_END_S: float = TRAIN_END_S + PURGE_GAP_S  # 42180.0
VAL_END_S: float = 51900.0  # 42180 + 3 * 3240 s (2.7 h)
PURGE2_END_S: float = VAL_END_S + PURGE_GAP_S  # 51960.0
TOTAL_DURATION_S: float = DATASET_HOURS * 3600.0  # 64800.0 s (18.0 h)

EPOCH_UTC = datetime(2026, 1, 15, 0, 0, 0, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class Episode:
    start_s: float
    end_s: float
    scenario: str
    sub_mode: str = ""


def _assign_raw_split(time_s: float) -> str:
    if time_s < TRAIN_END_S:
        return "train"
    if time_s < PURGE1_END_S:
        return "purge"
    if time_s < VAL_END_S:
        return "val"
    if time_s < PURGE2_END_S:
        return "purge"
    return "test"


def _build_macro_cycle_episodes(
    cycle_start_s: float,
    cycle_duration_s: float,
    rng: np.random.Generator,
    *,
    cycle_index: int,
) -> list[Episode]:
    """Build one macro-cycle containing nominal cushions and all 9 fault scenarios."""
    episodes: list[Episode] = []
    cursor = cycle_start_s
    cycle_end = cycle_start_s + cycle_duration_s

    # Cycle 0 starts with 120s nominal + 130s cadence_drift so raw_sample_500.csv
    # (0.0 .. 249.5 s) displays two distinct physical operating regimes.
    if cycle_index == 0:
        episodes.append(Episode(cursor, cursor + 120.0, "nominal"))
        cursor += 120.0
        episodes.append(Episode(cursor, cursor + 130.0, "cadence_drift", sub_mode="fast"))
        cursor += 130.0
        remaining_faults = [s for s in FAULT_SCENARIOS if s != "cadence_drift"]
        rng.shuffle(remaining_faults)
    else:
        remaining_faults = list(FAULT_SCENARIOS)
        rng.shuffle(remaining_faults)

    scale = cycle_duration_s / 3240.0
    multi_modes = ["level_chatter", "overtemp_watchdog", "overtemp_estop"]

    for idx, fault in enumerate(remaining_faults):
        cushion_s = float(rng.integers(130, 190)) * scale
        # Round to 0.5 s grid
        cushion_s = round(cushion_s * 2.0) / 2.0
        episodes.append(Episode(cursor, cursor + cushion_s, "nominal"))
        cursor += cushion_s

        if fault == "cadence_drift":
            dur_s = float(rng.integers(105, 130))
            sub = "slow" if (cycle_index + idx) % 2 == 0 else "fast"
        elif fault == "chattering_b1":
            dur_s = float(rng.integers(70, 95))
            sub = "bounce"
        elif fault == "conveyor_blockage":
            dur_s = float(rng.integers(70, 95))
            sub = "stuck_high" if rng.random() < 0.25 else "stuck_low"
        elif fault == "intermittent_high_level":
            dur_s = float(rng.integers(80, 110))
            sub = "intermittent"
        elif fault == "sustained_high_level":
            dur_s = float(rng.integers(55, 80))
            sub = "sustained"
        elif fault == "overtemperature":
            dur_s = float(rng.integers(50, 75))
            sub = "thermal"
        elif fault == "watchdog_timeout":
            dur_s = float(rng.integers(50, 75))
            sub = "wd"
        elif fault == "emergency_stop":
            dur_s = float(rng.integers(45, 70))
            sub = "estop"
        else:  # multi_fault
            dur_s = float(rng.integers(55, 80))
            sub = multi_modes[(cycle_index + idx) % len(multi_modes)]

        dur_s = round(dur_s * 2.0) / 2.0
        if cursor + dur_s > cycle_end - 90.0:
            dur_s = max(45.0, round((cycle_end - 90.0 - cursor) * 2.0) / 2.0)
        episodes.append(Episode(cursor, cursor + dur_s, fault, sub_mode=sub))
        cursor += dur_s

    if cursor < cycle_end:
        episodes.append(Episode(cursor, cycle_end, "nominal"))

    return episodes


def _build_full_schedule(rng: np.random.Generator) -> list[Episode]:
    """Build deterministic 18h schedule aligned cleanly with train/purge/val/purge/test."""
    episodes: list[Episode] = []

    # Train: 13 cycles of 3240 s = 42120 s
    for c in range(13):
        episodes.extend(_build_macro_cycle_episodes(c * 3240.0, 3240.0, rng, cycle_index=c))

    # Purge 1: forced steady nominal [42120, 42180)
    episodes.append(Episode(TRAIN_END_S, PURGE1_END_S, "nominal", sub_mode="purge"))

    # Val: 3 cycles of 3240 s = 9720 s [42180, 51900)
    for c in range(3):
        episodes.extend(
            _build_macro_cycle_episodes(PURGE1_END_S + c * 3240.0, 3240.0, rng, cycle_index=100 + c)
        )

    # Purge 2: forced steady nominal [51900, 51960)
    episodes.append(Episode(VAL_END_S, PURGE2_END_S, "nominal", sub_mode="purge"))

    # Test: 3 cycles of 3240 s + 1 cycle of 3120 s = 12840 s [51960, 64800)
    for c in range(3):
        episodes.extend(
            _build_macro_cycle_episodes(PURGE2_END_S + c * 3240.0, 3240.0, rng, cycle_index=200 + c)
        )
    episodes.extend(
        _build_macro_cycle_episodes(PURGE2_END_S + 3 * 3240.0, 3120.0, rng, cycle_index=203)
    )
    return episodes


def _compute_plc_outputs(
    *,
    di_nivel_alto: bool,
    di_sobretemp: bool,
    flag_emerg_travada: bool,
    flag_watchdog: bool,
    scenario: str,
) -> tuple[int, int, int]:
    """Compute (estado_clp, mascara_do_real, cod_intertrav) matching FB_SafetyInterlock."""
    if flag_emerg_travada:
        # Priority 1: Emergency latched -> DO0=0, DO1=0, DO2=1, DO3=0
        return ESTADO_EMERGENCIA_TRAV, 0b0100, INTERTRAV_EMERGENCIA
    if di_sobretemp:
        # Priority 2: Overtemperature -> DO0=0, DO1=1, DO2=1, DO3=NOT watchdog
        do3 = 0 if flag_watchdog else 1
        mascara = (do3 << 3) | 0b0110
        return ESTADO_ALERTA, mascara, INTERTRAV_SOBRETEMP
    if flag_watchdog:
        # Priority 3: Watchdog -> DO0=0, DO1=di_nivel_alto, DO2=1, DO3=0
        mascara = ((1 if di_nivel_alto else 0) << 1) | 0b0100
        return ESTADO_MODO_DEGRADADO, mascara, INTERTRAV_WATCHDOG

    # Priority 4: Remote AUTO enabled
    do0 = 0 if scenario in {"sustained_high_level", "conveyor_blockage"} else 1
    do1 = 1 if di_nivel_alto else 0
    do2 = 1 if scenario in {"sustained_high_level", "chattering_b1", "conveyor_blockage"} else 0
    do3 = 1
    mascara = (do3 << 3) | (do2 << 2) | (do1 << 1) | do0
    alerta = di_nivel_alto or (scenario != "nominal")
    estado = ESTADO_ALERTA if alerta else ESTADO_NORMAL_AUTO
    return estado, mascara, INTERTRAV_NENHUM


def simulate_raw_telemetry(
    *,
    seed: int = SEED,
    duration_hours: float = DATASET_HOURS,
) -> pd.DataFrame:
    """Generate 2 Hz raw PLC500 telemetry dataframe with UINT16 counters and wrap-arounds."""
    rng = np.random.default_rng(seed)
    total_s = duration_hours * 3600.0
    n_samples = int(round(total_s * SAMPLE_RATE_HZ))

    episodes = _build_full_schedule(rng)

    # Pre-allocate columns for fast vectorized construction
    sample_idx = np.arange(n_samples, dtype=np.int64)
    time_s_arr = sample_idx * TP

    timestamps = [
        (EPOCH_UTC + timedelta(seconds=float(t))).isoformat().replace("+00:00", "Z")
        for t in time_s_arr
    ]

    scenarios_arr: list[str] = ["nominal"] * n_samples
    splits_arr: list[str] = [_assign_raw_split(float(t)) for t in time_s_arr]

    di_presenca = np.zeros(n_samples, dtype=bool)
    di_nivel_alto = np.zeros(n_samples, dtype=bool)
    di_sobretemp = np.zeros(n_samples, dtype=bool)
    di_emergencia_ok = np.ones(n_samples, dtype=bool)
    flag_emerg_travada = np.zeros(n_samples, dtype=bool)
    flag_watchdog = np.zeros(n_samples, dtype=bool)

    cnt_presenca = np.zeros(n_samples, dtype=np.int64)
    cnt_nivel_alto = np.zeros(n_samples, dtype=np.int64)
    cnt_sobretemp = np.zeros(n_samples, dtype=np.int64)
    cnt_emergencia = np.zeros(n_samples, dtype=np.int64)

    estado_clp = np.zeros(n_samples, dtype=np.int64)
    mascara_do_real = np.zeros(n_samples, dtype=np.int64)
    cod_intertrav = np.zeros(n_samples, dtype=np.int64)

    # Persistent UINT16 counters calibrated to produce genuine 65535 -> 0 wrap-arounds
    c_pres = 65200
    c_nivel = 65500
    c_temp = 65520
    c_emerg = 65525

    prev_nivel = False
    prev_temp = False
    prev_emerg_trip = False

    # Next scheduled B1 pulse timestamp for paced conveyor events
    next_b1_time = 2.0

    for ep in episodes:
        i_start = max(0, int(round(ep.start_s / TP)))
        i_end = min(n_samples, int(round(ep.end_s / TP)))
        if i_start >= i_end:
            continue

        ep_len = i_end - i_start
        ep_scen = ep.scenario
        ep_sub = ep.sub_mode

        # Reset B1 arrival clock on episode entry
        if ep_scen == "nominal":
            lam_b1 = float(rng.uniform(11.0, 13.0))
        elif ep_scen == "cadence_drift":
            lam_b1 = (
                float(rng.uniform(4.6, 5.6)) if ep_sub == "slow" else float(rng.uniform(18.2, 19.2))
            )
        elif ep_scen == "chattering_b1":
            lam_b1 = float(rng.uniform(28.0, 46.0))
        elif ep_scen == "conveyor_blockage":
            lam_b1 = float(rng.uniform(0.0, 1.5))
        elif ep_scen == "intermittent_high_level":
            lam_b1 = float(rng.uniform(8.5, 10.5))
        elif ep_scen == "sustained_high_level":
            lam_b1 = float(rng.uniform(0.5, 3.0))
        elif ep_scen in {"overtemperature", "watchdog_timeout", "emergency_stop"}:
            lam_b1 = float(rng.uniform(0.0, 1.5))
        else:  # multi_fault
            if ep_sub == "level_chatter":
                lam_b1 = float(rng.uniform(28.0, 42.0))
            else:
                lam_b1 = float(rng.uniform(0.0, 1.5))

        if lam_b1 > 0.0:
            mean_period = 60.0 / lam_b1
            next_b1_time = min(next_b1_time, ep.start_s + float(rng.uniform(0.5, mean_period)))
        else:
            mean_period = 9999.0
            next_b1_time = ep.end_s + 1.0

        # Precompute episode-level fault profiles
        estop_press_end_idx = i_start + int(round(0.65 * ep_len))
        if ep_scen == "intermittent_high_level":
            # Periodic 6-10 s ON bursts separated by 24-34 s OFF -> duty in [0.10, 0.25]
            on_steps = int(rng.integers(12, 18))
            period_steps = int(rng.integers(64, 84))
            phase = int(rng.integers(0, period_steps))
        else:
            on_steps = 0
            period_steps = 1
            phase = 0

        for idx in range(i_start, i_end):
            t_cur = idx * TP
            scenarios_arr[idx] = ep_scen

            # Physical DI1..DI3 + Watchdog states
            cur_nivel = False
            cur_temp = False
            cur_emerg_ok = True
            cur_emerg_latched = False
            cur_wd = False

            if ep_scen == "intermittent_high_level":
                cur_nivel = ((idx - i_start + phase) % period_steps) < on_steps
            elif ep_scen == "sustained_high_level":
                cur_nivel = True
            elif ep_scen == "overtemperature":
                cur_temp = True
            elif ep_scen == "watchdog_timeout":
                cur_wd = True
            elif ep_scen == "emergency_stop":
                cur_emerg_ok = idx >= estop_press_end_idx
                cur_emerg_latched = True
            elif ep_scen == "multi_fault":
                if ep_sub == "level_chatter":
                    cur_nivel = True
                elif ep_sub == "overtemp_watchdog":
                    cur_temp = True
                    cur_wd = True
                else:  # overtemp_estop
                    cur_temp = True
                    cur_emerg_ok = idx >= estop_press_end_idx
                    cur_emerg_latched = True

            # Physical B1 sensor & counter logic
            cur_b1 = False
            if ep_scen == "chattering_b1" or (
                ep_scen == "multi_fault" and ep_sub == "level_chatter"
            ):
                # High-frequency bounce: multiple edges + rapid 2Hz toggling
                p_edge = min(0.95, lam_b1 / 120.0)
                if rng.random() < p_edge:
                    pulses = int(rng.choice([1, 1, 2], p=[0.6, 0.25, 0.15]))
                    c_pres = (c_pres + pulses) % UINT16_MOD
                    cur_b1 = True
                else:
                    cur_b1 = bool(rng.random() < 0.35 and not di_presenca[max(0, idx - 1)])
            elif ep_scen == "conveyor_blockage" and ep_sub == "stuck_high":
                cur_b1 = True  # Optical beam continuously blocked without new counts
            else:
                if lam_b1 > 0.0 and t_cur >= next_b1_time:
                    c_pres = (c_pres + 1) % UINT16_MOD
                    cur_b1 = True
                    jitter = 0.08 if ep_scen == "nominal" else 0.15
                    step = max(0.5, mean_period * float(rng.uniform(1.0 - jitter, 1.0 + jitter)))
                    next_b1_time = t_cur + step

            # Rising-edge counters for DI1, DI2, and falling-edge (NOT DI3) for DI3
            if cur_nivel and not prev_nivel:
                c_nivel = (c_nivel + 1) % UINT16_MOD
            if cur_temp and not prev_temp:
                c_temp = (c_temp + 1) % UINT16_MOD
            cur_emerg_trip = not cur_emerg_ok
            if cur_emerg_trip and not prev_emerg_trip:
                c_emerg = (c_emerg + 1) % UINT16_MOD

            prev_nivel = cur_nivel
            prev_temp = cur_temp
            prev_emerg_trip = cur_emerg_trip

            di_presenca[idx] = cur_b1
            di_nivel_alto[idx] = cur_nivel
            di_sobretemp[idx] = cur_temp
            di_emergencia_ok[idx] = cur_emerg_ok
            flag_emerg_travada[idx] = cur_emerg_latched
            flag_watchdog[idx] = cur_wd

            cnt_presenca[idx] = c_pres
            cnt_nivel_alto[idx] = c_nivel
            cnt_sobretemp[idx] = c_temp
            cnt_emergencia[idx] = c_emerg

            est, masc, cod = _compute_plc_outputs(
                di_nivel_alto=cur_nivel,
                di_sobretemp=cur_temp,
                flag_emerg_travada=cur_emerg_latched,
                flag_watchdog=cur_wd,
                scenario=ep_scen,
            )
            estado_clp[idx] = est
            mascara_do_real[idx] = masc
            cod_intertrav[idx] = cod

    return pd.DataFrame(
        {
            "sample_idx": sample_idx,
            "time_s": time_s_arr,
            "timestamp_utc": timestamps,
            "split": splits_arr,
            "scenario": scenarios_arr,
            "di_presenca": di_presenca,
            "di_nivel_alto": di_nivel_alto,
            "di_sobretemp": di_sobretemp,
            "di_emergencia_ok": di_emergencia_ok,
            "flag_emerg_travada": flag_emerg_travada,
            "flag_watchdog": flag_watchdog,
            "cnt_presenca": cnt_presenca,
            "cnt_nivel_alto": cnt_nivel_alto,
            "cnt_sobretemp": cnt_sobretemp,
            "cnt_emergencia": cnt_emergencia,
            "estado_clp": estado_clp,
            "mascara_do_real": mascara_do_real,
            "cod_intertrav": cod_intertrav,
        }
    )


def _count_wrap_arounds(series: pd.Series) -> int:
    arr = series.to_numpy(dtype=np.int64)
    if len(arr) < 2:
        return 0
    return int(np.sum(arr[1:] < arr[:-1]))


def build_sliding_windows(raw_df: pd.DataFrame) -> pd.DataFrame:
    """Slice raw 2 Hz telemetry into W=60s (K=120) windows per split (~3,688 windows)."""
    records: list[dict[str, Any]] = []
    window_id = 0
    stride_samples = 35  # 17.5 s stride -> 3,688 windows across 18h (in [3600, 3800])

    split_ranges = (
        ("train", 0, int(round(TRAIN_END_S / TP))),
        ("val", int(round(PURGE1_END_S / TP)), int(round(VAL_END_S / TP))),
        ("test", int(round(PURGE2_END_S / TP)), len(raw_df)),
    )

    sample_dicts = raw_df.to_dict(orient="records")

    for split_name, idx_min, idx_max in split_ranges:
        pos = idx_min
        while pos + K <= idx_max:
            end_pos = pos + K
            win_samples = sample_dicts[pos:end_pos]
            wf = extract_features_from_raw_window(
                win_samples,
                window_s=W,
                mu=BASELINE_MU,
                sigma=BASELINE_SIGMA,
                eps=BASELINE_EPS,
            )
            stat = evaluate_statistical_fallback(wf)
            sev = stat.severity_code

            if sev == 0:
                win_scenario = "nominal"
            else:
                non_nom = {s["scenario"] for s in win_samples if s["scenario"] != "nominal"}
                if len(non_nom) == 1:
                    win_scenario = next(iter(non_nom))
                elif len(non_nom) >= 2:
                    win_scenario = "multi_fault"
                else:
                    win_scenario = "cadence_drift"

            row: dict[str, Any] = {
                "window_id": window_id,
                "split": split_name,
                "window_start_s": float(win_samples[0]["time_s"]),
                "window_end_s": float(win_samples[0]["time_s"]) + W,
                "raw_start_idx": pos,
                "raw_end_idx": end_pos - 1,
                "timestamp_start": win_samples[0]["timestamp_utc"],
                "timestamp_end": win_samples[-1]["timestamp_utc"],
                "amostras_k": wf.amostras_k,
                "lambda1": float(wf.taxa_presenca_min),
                "z1": float(wf.zscore_presenca),
            }
            for col in MODEL_FEATURE_COLUMNS:
                val = getattr(wf, col)
                row[col] = int(val) if isinstance(val, bool) else float(val)
            for col in AUDIT_FEATURE_COLUMNS:
                val = getattr(wf, col)
                row[col] = int(val) if isinstance(val, int) else float(val)

            row["severity"] = sev
            row["severity_label"] = SEVERITY_CODE_TO_NAME[sev]
            row["scenario"] = win_scenario
            records.append(row)
            window_id += 1
            pos += stride_samples

    return pd.DataFrame(records)


def _compute_summary(raw_df: pd.DataFrame, win_df: pd.DataFrame) -> dict[str, Any]:
    train_df = win_df[win_df["split"] == "train"]
    val_df = win_df[win_df["split"] == "val"]
    test_df = win_df[win_df["split"] == "test"]

    train_val_gap_s = float(val_df["window_start_s"].min() - train_df["window_end_s"].max())
    val_test_gap_s = float(test_df["window_start_s"].min() - val_df["window_end_s"].max())

    wrap_counts = {
        "cnt_presenca": _count_wrap_arounds(raw_df["cnt_presenca"]),
        "cnt_nivel_alto": _count_wrap_arounds(raw_df["cnt_nivel_alto"]),
        "cnt_sobretemp": _count_wrap_arounds(raw_df["cnt_sobretemp"]),
        "cnt_emergencia": _count_wrap_arounds(raw_df["cnt_emergencia"]),
    }

    split_summary: dict[str, Any] = {}
    for split_name, df_s in (("train", train_df), ("val", val_df), ("test", test_df)):
        split_summary[split_name] = {
            "window_count": int(len(df_s)),
            "ratio": round(float(len(df_s)) / float(len(win_df)), 4),
            "window_start_s_min": float(df_s["window_start_s"].min()),
            "window_end_s_max": float(df_s["window_end_s"].max()),
            "raw_idx_min": int(df_s["raw_start_idx"].min()),
            "raw_idx_max": int(df_s["raw_end_idx"].max()),
            "severity_counts": {
                str(k): int(v) for k, v in df_s["severity"].value_counts().sort_index().items()
            },
            "scenario_counts": {
                str(k): int(v) for k, v in df_s["scenario"].value_counts().sort_index().items()
            },
        }

    return {
        "seed": SEED,
        "duration_hours": DATASET_HOURS,
        "sample_rate_hz": SAMPLE_RATE_HZ,
        "window_seconds": W,
        "samples_per_window": K,
        "stride_seconds": STRIDE_SAMPLES * TP,
        "raw_sample_count": int(len(raw_df)),
        "window_count": int(len(win_df)),
        "model_feature_columns": list(MODEL_FEATURE_COLUMNS),
        "audit_feature_columns": list(AUDIT_FEATURE_COLUMNS),
        "temporal_purge_audit": {
            "purge_gap_required_s": PURGE_GAP_S,
            "train_val_gap_s": train_val_gap_s,
            "val_test_gap_s": val_test_gap_s,
            "overlapping_raw_samples": 0,
            "leakage_free": bool(train_val_gap_s >= PURGE_GAP_S and val_test_gap_s >= PURGE_GAP_S),
        },
        "wrap_around_events": wrap_counts,
        "total_wrap_arounds": int(sum(wrap_counts.values())),
        "scenarios": {
            str(k): int(v) for k, v in win_df["scenario"].value_counts().sort_index().items()
        },
        "splits": split_summary,
    }


def _write_data_docs(data_dir: Path, summary: dict[str, Any]) -> None:
    raw_count = summary["raw_sample_count"]
    win_count = summary["window_count"]
    splits = summary["splits"]
    gap_audit = summary["temporal_purge_audit"]

    dict_lines = [
        "# Data Dictionary — WEG PLC500 Synthetic Industrial Telemetry",
        "",
        "## 1. Raw 2 Hz Telemetry (`data/raw/raw_telemetry_2hz.csv.gz` & `raw_sample_500.csv`)",
        "",
        f"- **Rows**: `{raw_count:,}` (18.0 hours @ 2.0 Hz, `dt = 0.5 s`, `seed = {SEED}`)",
        "- **Sample file**: `raw_sample_500.csv` contains the first `500` rows "
        "(`0.0 s .. 249.5 s`) covering `nominal` followed by `cadence_drift`.",
        "",
        "| Column | Type | Unit / Range | PLC500 / Modbus Source | Description |",
        "|---|---|---|---|---|",
        f"| `sample_idx` | `int64` | `0..{raw_count - 1}` | — | Monotonic 2 Hz sample index |",
        "| `time_s` | `float64` | `0.0..64799.5` s | — | Elapsed simulation time in seconds |",
        "| `timestamp_utc` | `string` | ISO-8601 UTC | — | "
        "Synthetic UTC timestamp (`2026-01-15T00:00:00Z` + `time_s`) |",
        "| `split` | `string` | `train`, `purge`, `val`, `test` | — | Strict temporal partition |",
        "| `scenario` | `string` | 10 scenarios | — | Active physical/OT scenario label |",
        "| `di_presenca` | `bool` | `False`/`True` | `FC02` addr `0` (`DI0`) | "
        "Debounced presence sensor `B1_SensPresenca` |",
        "| `di_nivel_alto` | `bool` | `False`/`True` | `FC02` addr `1` (`DI1`) | "
        "Debounced high-level sensor `B2_SensNivelAlto` |",
        "| `di_sobretemp` | `bool` | `False`/`True` | `FC02` addr `2` (`DI2`) | "
        "Debounced overtemperature sensor `B3_SensSobretemp` |",
        "| `di_emergencia_ok` | `bool` | `False`/`True` | `FC02` addr `3` (`DI3`) | "
        "Fail-safe NC E-stop (`False` = trip/wire break) |",
        "| `flag_emerg_travada` | `bool` | `False`/`True` | `FC02` addr `4` | "
        "Latched emergency interlock flag |",
        "| `flag_watchdog` | `bool` | `False`/`True` | `FC02` addr `5` | "
        "Active Modbus heartbeat watchdog timeout flag |",
        "| `cnt_presenca` | `uint16` | `0..65535` | `FC04` addr `0` (`IR[0]`) | "
        "Modular 16-bit rising-edge counter for `B1` |",
        "| `cnt_nivel_alto` | `uint16` | `0..65535` | `FC04` addr `1` (`IR[1]`) | "
        "Modular 16-bit rising-edge counter for `B2` |",
        "| `cnt_sobretemp` | `uint16` | `0..65535` | `FC04` addr `2` (`IR[2]`) | "
        "Modular 16-bit rising-edge counter for `B3` |",
        "| `cnt_emergencia` | `uint16` | `0..65535` | `FC04` addr `3` (`IR[3]`) | "
        "Modular 16-bit trip counter for `B4` (`NOT DI3`) |",
        "| `estado_clp` | `int` | `0..4` | `FC04` addr `4` (`IR[4]`) | "
        "PLC FSM state (`0=INIT..4=EMERGENCIA_TRAV`) *(audit only, excluded from ML `X`)* |",
        "| `mascara_do_real` | `int` | `0..15` | `FC04` addr `5` (`IR[5]`) | "
        "Real output bitmask `DO0..DO3` *(audit only, excluded from ML `X`)* |",
        "| `cod_intertrav` | `int` | `0..3` | `FC04` addr `7` (`IR[7]`) | "
        "Active local interlock code *(audit only, excluded from ML `X`)* |",
        "",
        "## 2. Processed 60 s Sliding-Window Features (`data/processed/window_features_60s.csv`)",
        "",
        f"- **Rows**: `{win_count:,}` windows (`W = 60.0 s`, `K = 120` samples, `stride = 17.5 s`)",
        "- **Zero Train/Serve Skew**: `MODEL_FEATURE_COLUMNS` (`10` columns) are computed "
        "identically in raw 2 Hz mode and summary `/predict` mode.",
        "",
        "### Metadata & Partition Columns (Excluded from ML `X`)",
        "- `window_id`, `split`, `window_start_s`, `window_end_s`, `raw_start_idx`, "
        "`raw_end_idx`, `timestamp_start`, `timestamp_end`, `amostras_k`, `lambda1`, `z1`",
        "",
        "### Model Input Features (`MODEL_FEATURE_COLUMNS` — 10 Leakage-Free Features)",
        "| Column | Range | Formula / Definition |",
        "|---|---|---|",
        "| `taxa_presenca_min` | `>= 0.0` | "
        "`(deltaMod16(cnt_presenca_end, cnt_presenca_start) / 60.0) * 60.0` [events/min] |",
        "| `zscore_presenca` | `float` | `(taxa_presenca_min - 12.0) / (2.5 + 0.01)` |",
        "| `abs_zscore_presenca` | `>= 0.0` | `abs(zscore_presenca)` |",
        "| `duty_nivel` | `[0.0, 1.0]` | Mean of `di_nivel_alto` across window samples |",
        "| `duty_sobretemp` | `[0.0, 1.0]` | Mean of `di_sobretemp` across window samples |",
        "| `chattering_index` | `[0.0, 1.0]` | "
        "Bounded rapid oscillation & multi-pulse rate excess index |",
        "| `veto_emergencia` | `{0, 1}` | "
        "`1` if `flag_emerg_travada` or `not di_emergencia_ok` at window end |",
        "| `veto_sobretemp` | `{0, 1}` | "
        "`1` if `duty_sobretemp > 0` or `di_sobretemp` at window end |",
        "| `veto_watchdog` | `{0, 1}` | `1` if `flag_watchdog` at window end |",
        "| `veto_nivel_sustentado` | `{0, 1}` | `1` if `duty_nivel >= 0.30` |",
        "",
        "### Audit Columns (Retained for OT/IT Traceability, Excluded from ML `X`)",
        "- `delta_cnt_presenca`, `delta_cnt_nivel`, `delta_cnt_sobretemp`, `delta_cnt_emergencia`",
        "- `duty_presenca`, `duty_emerg_ok`, `duty_emerg_travada`, `duty_watchdog`",
        "",
        "### Target Columns (Excluded from ML `X`)",
        "- `severity` (`0=NORMAL`, `1=ATENCAO`, `2=CRITICO`, `3=EMERGENCIA`)",
        "- `severity_label` (`NORMAL`, `ATENCAO`, `CRITICO`, `EMERGENCIA`)",
        "- `scenario` (`nominal`, `cadence_drift`, `chattering_b1`, `conveyor_blockage`, "
        "`intermittent_high_level`, `sustained_high_level`, `overtemperature`, "
        "`watchdog_timeout`, `emergency_stop`, `multi_fault`)",
        "",
    ]
    dict_md = "\n".join(dict_lines)

    tr_s, va_s, te_s = splits["train"], splits["val"], splits["test"]
    readme_lines = [
        "# WEG PLC500 Synthetic Telemetry Dataset",
        "",
        f"Reproducible 18-hour @ 2 Hz (`{raw_count:,}` raw samples, `seed = {SEED}`) "
        "industrial bench dataset for anomaly detection, severity classification, and "
        "safety-rule validation on the **WEG PLC500** supervisory pipeline.",
        "",
        "## Files",
        "",
        f"- `raw/raw_telemetry_2hz.csv.gz` — Full 18h @ 2 Hz raw telemetry (`{raw_count:,}` rows, "
        "gzip-compressed CSV, no Git LFS).",
        "- `raw/raw_sample_500.csv` — First `500` rows (`0.0 s .. 249.5 s`) for quick inspection.",
        f"- `processed/window_features_60s.csv` — `{win_count:,}` sliding windows "
        "(`W = 60 s`, `K = 120` samples) with strict temporal `split` column.",
        "- `processed/scenario_summary.json` — Split statistics, purge gap proof, "
        "scenario counts, and `UINT16` wrap-around audit.",
        "- `data_dictionary.md` — Column-by-column schema and Modbus register mapping.",
        "",
        "## Strict Temporal Split & Purge Gaps (Zero Leakage)",
        "",
        "```text",
        "[ train: 0..42120 s ] --(60 s purge)-- "
        "[ val: 42180..51900 s ] --(60 s purge)-- [ test: 51960..64800 s ]",
        "```",
        "",
        "| Split | Windows | Ratio | Raw Index Range | Purge Gap to Next Split |",
        "|---|---:|---:|---|---|",
        f"| `train` | {tr_s['window_count']:,} | {tr_s['ratio'] * 100:.2f}% | "
        f"`{tr_s['raw_idx_min']}..{tr_s['raw_idx_max']}` | "
        f"`{gap_audit['train_val_gap_s']:.1f} s` |",
        f"| `val` | {va_s['window_count']:,} | {va_s['ratio'] * 100:.2f}% | "
        f"`{va_s['raw_idx_min']}..{va_s['raw_idx_max']}` | "
        f"`{gap_audit['val_test_gap_s']:.1f} s` |",
        f"| `test` | {te_s['window_count']:,} | {te_s['ratio'] * 100:.2f}% | "
        f"`{te_s['raw_idx_min']}..{te_s['raw_idx_max']}` | — |",
        "",
        "## Regeneration",
        "",
        "```bash",
        "/home/augusto_dantas/sla/.venv/bin/python -m plc500_ml.data_generator",
        "```",
        "",
    ]
    readme_md = "\n".join(readme_lines)
    (data_dir / "data_dictionary.md").write_text(dict_md, encoding="utf-8")
    (data_dir / "README.md").write_text(readme_md, encoding="utf-8")


def generate_dataset(
    *,
    output_dir: Path | str | None = None,
    seed: int = SEED,
    duration_hours: float = DATASET_HOURS,
) -> dict[str, Any]:
    """Generate all raw, processed, summary, and documentation files under data/."""
    data_dir = Path(output_dir) if output_dir is not None else DATA_DIR
    raw_dir = data_dir / "raw"
    proc_dir = data_dir / "processed"
    raw_dir.mkdir(parents=True, exist_ok=True)
    proc_dir.mkdir(parents=True, exist_ok=True)

    raw_df = simulate_raw_telemetry(seed=seed, duration_hours=duration_hours)
    win_df = build_sliding_windows(raw_df)
    summary = _compute_summary(raw_df, win_df)

    raw_path = raw_dir / RAW_DATA_PATH.name
    sample_path = raw_dir / RAW_SAMPLE_PATH.name
    proc_path = proc_dir / PROCESSED_FEATURES_PATH.name
    summary_path = proc_dir / SCENARIO_SUMMARY_PATH.name

    raw_df.to_csv(raw_path, index=False, compression="gzip")
    raw_df.iloc[:500].to_csv(sample_path, index=False)
    win_df.to_csv(proc_path, index=False)
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    _write_data_docs(data_dir, summary)

    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate PLC500 18h @ 2Hz synthetic dataset.")
    parser.add_argument("--output-dir", type=Path, default=DATA_DIR, help="Target data directory.")
    parser.add_argument("--seed", type=int, default=SEED, help="PRNG seed (default: 42).")
    args = parser.parse_args()

    summary = generate_dataset(output_dir=args.output_dir, seed=args.seed)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()

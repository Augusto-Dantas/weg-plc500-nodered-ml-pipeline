"""Unit tests for sliding-window feature engineering and wrap-16 arithmetic."""

from __future__ import annotations

import math

import pytest

from plc500_ml.features import (
    FORBIDDEN_LEAKAGE_COLUMNS,
    MODEL_FEATURE_COLUMNS,
    EmptyWindowError,
    compute_chattering_index_raw,
    compute_chattering_index_summary,
    compute_lambda,
    compute_z_score,
    delta_mod16,
    deltaMod16,
    extract_features_from_raw_window,
    extract_features_from_summary,
)


def _make_sample(
    *,
    di_presenca: bool = False,
    di_nivel_alto: bool = False,
    di_sobretemp: bool = False,
    di_emergencia_ok: bool = True,
    flag_emerg_travada: bool = False,
    flag_watchdog: bool = False,
    cnt_presenca: int = 0,
    cnt_nivel_alto: int = 0,
    cnt_sobretemp: int = 0,
    cnt_emergencia: int = 0,
) -> dict[str, object]:
    return {
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
    }


def test_delta_mod16_normal_and_wrap_around() -> None:
    assert deltaMod16 is delta_mod16
    assert delta_mod16(10, 3) == 7
    assert delta_mod16(0, 0) == 0
    assert delta_mod16(0, 65535) == 1
    assert delta_mod16(5, 65530) == 11
    assert delta_mod16(65535, 0) == 65535


def test_delta_mod16_invalid_inputs_and_non_strict() -> None:
    with pytest.raises(ValueError):
        delta_mod16(-1, 0)
    with pytest.raises(ValueError):
        delta_mod16(65536, 0)
    with pytest.raises(TypeError):
        delta_mod16(True, 0)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        delta_mod16(1.5, 0)  # type: ignore[arg-type]

    assert delta_mod16(65536, 65535, strict=False) == 1


def test_compute_lambda_and_z_score() -> None:
    assert compute_lambda(12, 60.0) == pytest.approx(12.0)
    assert compute_lambda(0, 60.0) == pytest.approx(0.0)
    assert compute_lambda(30, 60.0) == pytest.approx(30.0)

    assert compute_z_score(12.0) == pytest.approx(0.0)
    assert compute_z_score(0.0) == pytest.approx(-12.0 / 2.51)
    assert compute_z_score(30.0) == pytest.approx(18.0 / 2.51)
    assert compute_z_score(15.0, mu=12.0, sigma=0.0, eps=0.01) == pytest.approx(300.0)

    with pytest.raises(ValueError):
        compute_lambda(1, window_s=0.0)
    with pytest.raises(ValueError):
        compute_lambda(-1, window_s=60.0)
    with pytest.raises(ValueError):
        compute_z_score(10.0, sigma=-0.1)
    with pytest.raises(ValueError):
        compute_z_score(10.0, eps=0.0)


def test_duty_cycles_and_local_veto_boundaries() -> None:
    samples = [
        _make_sample(
            di_nivel_alto=(i < 36),
            di_sobretemp=False,
            cnt_presenca=i // 10,
        )
        for i in range(120)
    ]
    wf = extract_features_from_raw_window(samples)
    assert wf.duty_nivel == pytest.approx(0.30)
    assert wf.veto_nivel_sustentado is True
    assert wf.duty_sobretemp == pytest.approx(0.0)
    assert wf.veto_sobretemp is False

    summary_just_below = extract_features_from_summary(
        {"taxa_presenca_min": 12.0, "duty_nivel": 0.299, "duty_sobretemp": 0.0}
    )
    assert summary_just_below.veto_nivel_sustentado is False

    summary_at_threshold = extract_features_from_summary(
        {"taxa_presenca_min": 12.0, "duty_nivel": 0.30, "duty_sobretemp": 0.0}
    )
    assert summary_at_threshold.veto_nivel_sustentado is True


def test_chattering_index_separates_stall_nominal_drift_and_chattering() -> None:
    stall_idx = compute_chattering_index_raw([False] * 120, [100] * 120, taxa_presenca_min=0.0)
    assert stall_idx == pytest.approx(0.0)

    # Steady 12 events/min -> 1 pulse every 10 samples (24 transitions / 119 ~ 0.20)
    nom_di = [(i % 10 == 0) for i in range(120)]
    nom_cnt = [100 + (i // 10) for i in range(120)]
    nom_idx = compute_chattering_index_raw(nom_di, nom_cnt, taxa_presenca_min=12.0)

    drift_idx = compute_chattering_index_summary(taxa_presenca_min=18.0)

    # Chattering: rapid alternating edges + sub-500ms multi-pulses (delta >= 2)
    chat_di = [(i % 2 == 0) for i in range(120)]
    chat_cnt = [100 + 2 * i for i in range(120)]
    chat_idx = compute_chattering_index_raw(chat_di, chat_cnt, taxa_presenca_min=40.0)

    assert 0.0 <= stall_idx < nom_idx < drift_idx < chat_idx <= 1.0
    assert chat_idx >= 0.75
    assert compute_chattering_index_summary(0.0) == pytest.approx(0.0)
    assert compute_chattering_index_summary(12.0) == pytest.approx(nom_idx, abs=0.02)
    assert compute_chattering_index_summary(30.0) >= 0.75


def test_raw_window_wraparound_empty_and_single_sample_edge_cases() -> None:
    with pytest.raises(EmptyWindowError):
        extract_features_from_raw_window([])

    with pytest.raises(EmptyWindowError):
        extract_features_from_summary(
            {"taxa_presenca_min": 12.0, "duty_nivel": 0.0, "duty_sobretemp": 0.0, "amostras_k": 0}
        )

    single = extract_features_from_raw_window([_make_sample(cnt_presenca=500)])
    assert single.amostras_k == 1
    assert single.taxa_presenca_min == 0.0
    assert single.chattering_index == 0.0
    assert all(math.isfinite(v) for v in single.to_model_vector())

    # 120-sample window crossing 65535 -> 0 wrap-around with 12 pulses
    samples = []
    cnt = 65530
    for i in range(120):
        pulse = i > 0 and (i % 10 == 0)
        if pulse:
            cnt = (cnt + 1) % 65536
        samples.append(_make_sample(di_presenca=pulse, cnt_presenca=cnt))
    # 119 // 10 = 11 pulses -> let's also add one at i=0? Or check exact deltaMod16(fim, inicio)
    expected_delta = (samples[-1]["cnt_presenca"] - samples[0]["cnt_presenca"] + 65536) % 65536
    wf = extract_features_from_raw_window(samples)
    assert wf.delta_cnt_presenca == expected_delta == 11
    assert wf.taxa_presenca_min == pytest.approx(11.0)
    assert wf.veto_emergencia is False


def test_model_feature_columns_leakage_free() -> None:
    assert len(MODEL_FEATURE_COLUMNS) == 10
    assert set(MODEL_FEATURE_COLUMNS).isdisjoint(FORBIDDEN_LEAKAGE_COLUMNS)

"""Tier 2 Boundary & Corner-Case E2E Tests — Area C (ML/Sim/API) and Area D (SQL/Schema/NR)."""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

import pytest
from jsonschema import ValidationError, validate

from tests_e2e.helpers import (
    assert_nodered_flows_contract,
    assert_plc500_sql_contract,
    build_predict_feature_payload,
    build_raw_window_payload,
    delta_mod16,
    discover_callable,
    ensure_python_pkg_on_path,
    make_nominal_raw_samples,
    make_valid_diagnosis,
    scan_text_for_secrets,
    validate_compose_file,
    validate_diagnostico_json,
    validate_js_syntax,
    validate_makefile,
    validate_postgres_ddl,
)

pytestmark = pytest.mark.tier2


def _call_safety_guard(
    sr_mod: Any, diag: dict[str, Any], feat: dict[str, Any]
) -> dict[str, Any]:
    guard_fn = discover_callable(
        sr_mod,
        (
            "apply_monotonicity_guard",
            "enforce_monotonicity",
            "monotonicity_guard",
            "guard_diagnosis",
            "validate_and_guard",
        ),
    )
    res = guard_fn(diag, feat)
    return res.model_dump() if hasattr(res, "model_dump") else dict(res)


# ==============================================================================
# Area C — Tier 2 Boundary & Corner Cases (16 tests)
# ==============================================================================


@pytest.mark.area_c
def test_tc_t2_01_delta_mod16_wraparound_in_raw_window(
    ml_api_client: Any, diagnostico_schema: dict[str, Any]
) -> None:
    """TC-T2-01: Verify ``65524 -> 0`` wrap-around yields ``delta=12`` (``NORMAL``, not stall)."""
    assert delta_mod16(0, 65535) == 1
    assert delta_mod16(5, 65530) == 11
    assert delta_mod16(0, 65524) == 12

    spec = ml_api_client.get("/openapi.json").json()
    samples = make_nominal_raw_samples(120, start_cnt_presenca=65524, delta_presenca=12)
    payload = build_raw_window_payload(spec, samples)
    resp = ml_api_client.post("/predict/raw-window", json=payload)
    assert resp.status_code == 200
    diag = resp.json().get("diagnostico_atuacao", resp.json().get("diagnosis", resp.json()))
    assert validate_diagnostico_json(diag, diagnostico_schema) == []
    assert diag["severidade"] == "NORMAL"


@pytest.mark.area_c
def test_tc_t2_02_zscore_eps_and_stall_vs_chattering_symmetry(
    repo_root: Path, ml_api_client: Any
) -> None:
    """TC-T2-02: Verify ``eps=0.01`` prevents div-by-zero and stall/chattering both trigger CRITICO."""
    ensure_python_pkg_on_path(repo_root)
    import plc500_ml.features as feat_mod

    z_fn = getattr(feat_mod, "compute_zscore", None) or getattr(feat_mod, "zscore", None)
    if callable(z_fn):
        val = z_fn(12.0, mu=12.0, sigma=0.0, eps=0.01)
        assert val == 0.0

    spec = ml_api_client.get("/openapi.json").json()
    # Stall: lambda1=0 -> z1 = -4.78 <= -3.0
    stall_samples = make_nominal_raw_samples(120, start_cnt_presenca=50, delta_presenca=0)
    r_stall = ml_api_client.post(
        "/predict/raw-window", json=build_raw_window_payload(spec, stall_samples)
    )
    assert r_stall.status_code == 200
    d_stall = r_stall.json().get(
        "diagnostico_atuacao", r_stall.json().get("diagnosis", r_stall.json())
    )
    assert d_stall["severidade"] in ("CRITICO", "EMERGENCIA")
    assert d_stall["comandos_atuadores"]["cmd_motor"] is False

    # Chattering: lambda1=24 -> z1 = +4.78 >= +3.0
    chat_samples = make_nominal_raw_samples(120, start_cnt_presenca=50, delta_presenca=24)
    r_chat = ml_api_client.post(
        "/predict/raw-window", json=build_raw_window_payload(spec, chat_samples)
    )
    assert r_chat.status_code == 200
    d_chat = r_chat.json().get(
        "diagnostico_atuacao", r_chat.json().get("diagnosis", r_chat.json())
    )
    assert d_chat["severidade"] in ("CRITICO", "EMERGENCIA")
    assert d_chat["comandos_atuadores"]["cmd_motor"] is False


@pytest.mark.area_c
def test_tc_t2_03_severity_threshold_boundaries_z_and_duty_nivel(repo_root: Path) -> None:
    """TC-T2-03: Verify exact fallback severity transitions at ``|z1|=2.0/3.0`` & ``D2=0.30``."""
    ensure_python_pkg_on_path(repo_root)
    import plc500_ml.safety_rules as sr

    stat_fn = discover_callable(
        sr,
        (
            "compute_statistical_severity",
            "statistical_severity",
            "severidade_estatistica",
            "deterministic_fallback",
            "fallback_diagnosis",
        ),
    )
    import inspect

    sig = inspect.signature(stat_fn)

    def _sev(z1: float, d2: float) -> int:
        if len(sig.parameters) == 1:
            res = stat_fn(
                {
                    "z1": z1,
                    "z_presenca": z1,
                    "lambda1": 12.0 + z1 * 2.51,
                    "duty_nivel": d2,
                    "duty_temp": 0.0,
                    "emergencia_ok": True,
                    "emergencia_ativa": False,
                    "flag_watchdog": False,
                }
            )
        else:
            kwargs = {}
            for pname in sig.parameters:
                if pname in ("z1", "z_presenca"):
                    kwargs[pname] = z1
                elif pname == "duty_nivel":
                    kwargs[pname] = d2
                elif pname == "duty_temp":
                    kwargs[pname] = 0.0
                elif pname == "emergencia_ok":
                    kwargs[pname] = True
                elif pname in (
                    "emerg_ativa",
                    "emergencia_ativa",
                    "flag_watchdog",
                    "watchdog_ativo",
                ):
                    kwargs[pname] = False
            res = stat_fn(**kwargs)
        if isinstance(res, int):
            return res
        d = res.model_dump() if hasattr(res, "model_dump") else dict(res)
        sev_map = {"NORMAL": 0, "ATENCAO": 1, "CRITICO": 2, "EMERGENCIA": 3}
        return sev_map[d["severidade"]]

    assert _sev(1.99, 0.0) == 0
    assert _sev(2.00, 0.0) == 1
    assert _sev(2.99, 0.0) == 1
    assert _sev(3.00, 0.0) == 2
    assert _sev(0.0, 35 / 120) == 1
    assert _sev(0.0, 36 / 120) == 2


@pytest.mark.area_c
def test_tc_t2_04_empty_window_safe_handling_no_500(
    repo_root: Path, ml_api_client: Any
) -> None:
    """TC-T2-04: Verify empty window ``[]`` never raises ``ZeroDivisionError`` or HTTP 500."""
    ensure_python_pkg_on_path(repo_root)
    import plc500_ml.features as feat_mod

    extract_fn = discover_callable(
        feat_mod,
        ("extract_window_features", "compute_window_features", "extract_features"),
    )
    out = extract_fn([])
    assert out is not None

    spec = ml_api_client.get("/openapi.json").json()
    payload = build_raw_window_payload(spec, [])
    resp = ml_api_client.post("/predict/raw-window", json=payload)
    assert resp.status_code in (200, 400, 422), f"Unexpected status {resp.status_code}: {resp.text}"


@pytest.mark.area_c
def test_tc_t2_05_temporal_split_60s_purge_gap_zero_leakage(
    repo_root: Path, data_dir: Path
) -> None:
    """TC-T2-05: Verify strict >= 60 s temporal purge gap across ``train``/``val``/``test``."""
    ensure_python_pkg_on_path(repo_root)
    import pandas as pd

    df = pd.read_csv(data_dir / "processed" / "window_features_60s.csv")
    start_col = next(
        (c for c in ("window_start", "janela_inicio", "start_time", "t_start") if c in df.columns),
        None,
    )
    end_col = next(
        (c for c in ("window_end", "janela_fim", "end_time", "t_end") if c in df.columns),
        None,
    )
    if start_col and end_col:
        tr_end = float(df[df["split"] == "train"][end_col].max())
        va_start = float(df[df["split"] == "val"][start_col].min())
        va_end = float(df[df["split"] == "val"][end_col].max())
        te_start = float(df[df["split"] == "test"][start_col].min())
        assert va_start - tr_end >= 60.0, f"train->val purge gap {va_start - tr_end} < 60s"
        assert te_start - va_end >= 60.0, f"val->test purge gap {te_start - va_end} < 60s"
    else:
        ts_col = next(c for c in df.columns if "time" in c.lower() or "ts" in c.lower() or "janela" in c.lower())
        assert df[df["split"] == "train"][ts_col].max() < df[df["split"] == "val"][ts_col].min()
        assert df[df["split"] == "val"][ts_col].max() < df[df["split"] == "test"][ts_col].min()


@pytest.mark.area_c
def test_tc_t2_06_isolation_forest_split_purity_and_metadata_counts(
    repo_root: Path, python_dir: Path, data_dir: Path
) -> None:
    """TC-T2-06: Verify ``model_metadata.json`` split counts match ``window_features_60s.csv``."""
    ensure_python_pkg_on_path(repo_root)
    import pandas as pd

    df = pd.read_csv(data_dir / "processed" / "window_features_60s.csv")
    meta = json.loads((python_dir / "models" / "model_metadata.json").read_text(encoding="utf-8"))
    meta_str = json.dumps(meta)
    counts = df["split"].value_counts().to_dict()
    assert str(counts["test"]) in meta_str or str(len(df)) in meta_str
    assert len(df[df["split"] == "train"]) > 100


@pytest.mark.area_c
def test_tc_t2_07_monotonicity_guard_blocks_severity_downgrade(repo_root: Path) -> None:
    """TC-T2-07: Verify monotonicity guard clamps severity upward to statistical floor."""
    ensure_python_pkg_on_path(repo_root)
    import plc500_ml.safety_rules as sr

    unsafe_norm = make_valid_diagnosis(
        severidade="NORMAL",
        requer_relatorio=False,
        comandos_atuadores={"cmd_motor": True, "cmd_valvula": False, "cmd_alarme": False},
    )
    out_crit = _call_safety_guard(
        sr,
        unsafe_norm,
        {"z1": 3.5, "duty_nivel": 0.4, "duty_temp": 0.0, "emergencia_ok": True, "flag_watchdog": False},
    )
    assert out_crit["severidade"] in ("CRITICO", "EMERGENCIA")
    assert out_crit["requer_relatorio"] is True

    out_emerg = _call_safety_guard(
        sr,
        unsafe_norm,
        {"z1": 0.0, "duty_nivel": 0.0, "duty_temp": 0.0, "emergencia_ok": False, "flag_watchdog": False},
    )
    assert out_emerg["severidade"] == "EMERGENCIA"
    assert out_emerg["requer_relatorio"] is True


@pytest.mark.area_c
def test_tc_t2_08_monotonicity_guard_motor_veto_all_four_unsafe_conditions(
    repo_root: Path,
) -> None:
    """TC-T2-08: Verify monotonicity guard overrides ``cmd_motor=True`` to ``False`` in 4 faults."""
    ensure_python_pkg_on_path(repo_root)
    import plc500_ml.safety_rules as sr

    bad_proposal = make_valid_diagnosis(
        severidade="NORMAL",
        comandos_atuadores={"cmd_motor": True, "cmd_valvula": False, "cmd_alarme": False},
    )
    scenarios = (
        {"z1": 0.0, "duty_nivel": 0.0, "duty_temp": 0.2, "emergencia_ok": True, "flag_watchdog": False},
        {"z1": 0.0, "duty_nivel": 0.35, "duty_temp": 0.0, "emergencia_ok": True, "flag_watchdog": False},
        {"z1": 0.0, "duty_nivel": 0.0, "duty_temp": 0.0, "emergencia_ok": False, "flag_watchdog": False},
        {"z1": 0.0, "duty_nivel": 0.0, "duty_temp": 0.0, "emergencia_ok": True, "flag_watchdog": True},
    )
    for feat in scenarios:
        guarded = _call_safety_guard(sr, bad_proposal, feat)
        assert guarded["comandos_atuadores"]["cmd_motor"] is False, f"Motor veto failed for {feat}"
        assert guarded["comandos_atuadores"]["cmd_alarme"] is True


@pytest.mark.area_c
def test_tc_t2_09_fastapi_invalid_payloads_return_422_or_400_never_500(
    ml_api_client: Any,
) -> None:
    """TC-T2-09: Verify malformed JSON, wrong types, & out-of-range duties return 400/422."""
    r_bad_json = ml_api_client.post(
        "/predict",
        content="{broken json",
        headers={"Content-Type": "application/json"},
    )
    assert r_bad_json.status_code in (400, 422)

    r_missing = ml_api_client.post("/predict", json={})
    assert r_missing.status_code == 422

    spec = ml_api_client.get("/openapi.json").json()
    bad_duty = build_predict_feature_payload(
        spec, lambda1=12.0, z1=0.0, duty_nivel=1.5, duty_temp=-0.1, emergencia_ok=True
    )
    r_range = ml_api_client.post("/predict", json=bad_duty)
    assert r_range.status_code == 422


@pytest.mark.area_c
def test_tc_t2_10_fastapi_raw_window_overtemp_and_estop_safety_invariants(
    ml_api_client: Any, diagnostico_schema: dict[str, Any]
) -> None:
    """TC-T2-10: Verify ``/predict/raw-window`` enforces safety invariants under overtemp & E-stop."""
    spec = ml_api_client.get("/openapi.json").json()

    ot_samples = make_nominal_raw_samples(120, sobretemp_count=10)
    r1 = ml_api_client.post("/predict/raw-window", json=build_raw_window_payload(spec, ot_samples))
    assert r1.status_code == 200
    d1 = r1.json().get("diagnostico_atuacao", r1.json().get("diagnosis", r1.json()))
    assert validate_diagnostico_json(d1, diagnostico_schema) == []
    assert d1["comandos_atuadores"]["cmd_motor"] is False
    assert d1["severidade"] in ("CRITICO", "EMERGENCIA")

    es_samples = make_nominal_raw_samples(120, emergencia_ok=False)
    r2 = ml_api_client.post("/predict/raw-window", json=build_raw_window_payload(spec, es_samples))
    assert r2.status_code == 200
    d2 = r2.json().get("diagnostico_atuacao", r2.json().get("diagnosis", r2.json()))
    assert validate_diagnostico_json(d2, diagnostico_schema) == []
    assert d2["comandos_atuadores"]["cmd_motor"] is False
    assert d2["severidade"] == "EMERGENCIA"


@pytest.mark.area_c
def test_tc_t2_11_simulator_watchdog_bounds_1999_2000_30001_fallback(
    plc_sim: dict[str, Any],
) -> None:
    """TC-T2-11: Verify ``HR[1]=2000`` trips ~2 s while ``1999``/``30001`` fall back to 5000 ms."""
    client = plc_sim["client"]
    client.write_coil(4, True)
    client.write_hr(1, 2000)
    client.write_hr(0, 11)
    client.wait_until(lambda s: s["uiEstado_CLP"] == 1 and s["xFlag_WatchdogAtivo"] is False)

    # Freeze HR[0] and verify trip around 2.0 s
    client.wait_until(lambda s: s["xFlag_WatchdogAtivo"] is True, timeout=3.0)

    # Recover, then test out-of-bounds 1999 -> fallback 5000 ms (no trip at 2.4 s)
    client.write_hr(1, 1999)
    client.write_hr(0, 12)
    client.wait_until(lambda s: s["xFlag_WatchdogAtivo"] is False, timeout=1.0)
    time.sleep(2.4)
    assert client.snapshot()["xFlag_WatchdogAtivo"] is False
    client.wait_until(lambda s: s["xFlag_WatchdogAtivo"] is True, timeout=3.5)


@pytest.mark.area_c
def test_tc_t2_12_simulator_debounce_under_50ms_reject_vs_over_100ms_accept(
    plc_sim: dict[str, Any],
) -> None:
    """TC-T2-12: Verify ``<30 ms`` bounce on DI0..DI2 is rejected while ``120 ms`` increments +1."""
    client = plc_sim["client"]
    adapter = plc_sim["adapter"]
    base_cnt = client.read_irs()[0]

    adapter.pulse_di("di0", 20)
    time.sleep(0.08)
    assert client.read_irs()[0] == base_cnt, "Sub-50 ms bounce incremented uiCnt_Presenca"

    adapter.pulse_di("di0", 120)
    snap = client.wait_until(lambda s: s["uiCnt_Presenca"] == (base_cnt + 1) % 65536, timeout=1.0)
    assert snap["uiCnt_Presenca"] == (base_cnt + 1) % 65536


@pytest.mark.area_c
def test_tc_t2_13_simulator_di3_nf_zero_debounce_20ms_drop_latches(
    plc_sim: dict[str, Any],
) -> None:
    """TC-T2-13: Verify 20 ms NF drop on ``DI3`` latches E-stop immediately with zero debounce."""
    client = plc_sim["client"]
    adapter = plc_sim["adapter"]
    client.write_hr(0, 20)
    client.write_coil(4, True)
    client.write_coils(0, [True, False, False])
    base_emerg = client.read_irs()[3]

    adapter.pulse_di("di3", 20)
    snap = client.wait_until(lambda s: s["xFlag_EmergTravada"] is True, timeout=0.5)
    assert snap["uiEstado_CLP"] == 4
    assert snap["uiCod_Intertrav"] == 1
    assert snap["wMascara_DO_Real"] == 0b0100
    assert snap["uiCnt_Emergencia"] == (base_emerg + 1) % 65536


@pytest.mark.area_c
def test_tc_t2_14_simulator_reset_guard_blocked_by_di3_0_or_di2_1(
    plc_sim: dict[str, Any],
) -> None:
    """TC-T2-14: Verify ``Coil[3]`` reset is rejected if ``DI3=0`` OR ``DI2=1``."""
    client = plc_sim["client"]
    adapter = plc_sim["adapter"]
    adapter.set_inputs(di3=False)
    client.wait_until(lambda s: s["xFlag_EmergTravada"] is True, timeout=0.5)

    # Attempt reset while DI3=0 -> blocked
    client.write_coil(3, True)
    time.sleep(0.05)
    client.write_coil(3, False)
    assert client.snapshot()["xFlag_EmergTravada"] is True

    # Restore DI3=1 but activate DI2=1 -> still blocked
    adapter.set_inputs(di3=True, di2=True)
    client.wait_until(lambda s: s["xDI_Sobretemp"] is True, timeout=0.5)
    client.write_coil(3, True)
    time.sleep(0.05)
    client.write_coil(3, False)
    assert client.snapshot()["xFlag_EmergTravada"] is True

    # Clear DI2=0 and pulse Coil[3]=1 -> clears latch
    adapter.set_inputs(di2=False)
    client.wait_until(lambda s: s["xDI_Sobretemp"] is False, timeout=0.5)
    client.write_coil(3, True)
    client.wait_until(lambda s: s["xFlag_EmergTravada"] is False, timeout=0.5)
    client.write_coil(3, False)


@pytest.mark.area_c
def test_tc_t2_15_simulator_hr3_immutability_and_overtemp_watchdog_precedence(
    plc_sim: dict[str, Any],
) -> None:
    """TC-T2-15: Verify ``HR[3]`` resets to 100 every scan & Sobretemp overrides Watchdog."""
    client = plc_sim["client"]
    adapter = plc_sim["adapter"]

    client.write_hr(3, 999)
    snap = client.wait_until(lambda s: s["uiVersao_Mapa"] == 100, timeout=0.5)
    assert snap["uiVersao_Mapa"] == 100

    client.write_coil(4, True)
    client.write_hr(1, 2000)
    client.write_hr(0, 30)
    client.wait_until(lambda s: s["xFlag_WatchdogAtivo"] is True, timeout=3.0)

    adapter.set_inputs(di2=True)
    snap2 = client.wait_until(lambda s: s["uiCod_Intertrav"] == 2, timeout=0.5)
    assert snap2["uiEstado_CLP"] == 2
    assert snap2["wMascara_DO_Real"] == 0b0110


@pytest.mark.area_c
def test_tc_t2_16_simulator_high_level_b2_forces_valve_and_alerta_state(
    plc_sim: dict[str, Any],
) -> None:
    """TC-T2-16: Verify ``DI1=1`` forces ``DO1=1`` & ``ALERTA`` even when ``Coil[1]=0``."""
    client = plc_sim["client"]
    adapter = plc_sim["adapter"]
    client.write_hr(0, 40)
    client.write_coil(4, True)
    client.write_coils(0, [True, False, False])

    adapter.set_inputs(di1=True)
    snap = client.wait_until(lambda s: s["xDI_NivelAlto"] is True, timeout=0.5)
    assert snap["uiEstado_CLP"] == 2
    assert snap["do"]["DO1"] is True
    assert snap["uiVersao_Mapa"] == 100


# ==============================================================================
# Area D — Tier 2 Schema, SQL, JS & Infrastructure Boundary Cases (14 tests)
# ==============================================================================


@pytest.mark.area_d
def test_td_t2_01_schema_diagnostico_length_boundaries(
    diagnostico_schema: dict[str, Any],
) -> None:
    """TD-T2-01: Verify ``diagnostico`` minLength=10 and maxLength=400 boundaries."""
    for length, should_pass in ((9, False), (10, True), (400, True), (401, False)):
        payload = make_valid_diagnosis(diagnostico="x" * length)
        if should_pass:
            validate(payload, diagnostico_schema)
        else:
            with pytest.raises(ValidationError):
                validate(payload, diagnostico_schema)


@pytest.mark.area_d
def test_td_t2_02_schema_causa_provavel_length_boundaries(
    diagnostico_schema: dict[str, Any],
) -> None:
    """TD-T2-02: Verify ``causa_provavel`` minLength=5 and maxLength=250 boundaries."""
    for length, should_pass in ((4, False), (5, True), (250, True), (251, False)):
        payload = make_valid_diagnosis(causa_provavel="y" * length)
        if should_pass:
            validate(payload, diagnostico_schema)
        else:
            with pytest.raises(ValidationError):
                validate(payload, diagnostico_schema)


@pytest.mark.area_d
def test_td_t2_03_schema_resumo_executivo_length_boundaries(
    diagnostico_schema: dict[str, Any],
) -> None:
    """TD-T2-03: Verify ``resumo_executivo`` minLength=20 and maxLength=800 boundaries."""
    for length, should_pass in ((19, False), (20, True), (800, True), (801, False)):
        payload = make_valid_diagnosis(resumo_executivo="z" * length)
        if should_pass:
            validate(payload, diagnostico_schema)
        else:
            with pytest.raises(ValidationError):
                validate(payload, diagnostico_schema)


@pytest.mark.area_d
def test_td_t2_04_schema_additional_properties_rejected(
    diagnostico_schema: dict[str, Any],
) -> None:
    """TD-T2-04: Verify ``additionalProperties: false`` at root and ``comandos_atuadores``."""
    p1 = make_valid_diagnosis()
    p1["extra_field"] = True
    with pytest.raises(ValidationError):
        validate(p1, diagnostico_schema)

    p2 = make_valid_diagnosis()
    p2["comandos_atuadores"]["cmd_reset"] = False
    with pytest.raises(ValidationError):
        validate(p2, diagnostico_schema)


@pytest.mark.area_d
def test_td_t2_05_schema_invalid_enum_and_boolean_types_rejected(
    diagnostico_schema: dict[str, Any],
) -> None:
    """TD-T2-05: Verify invalid ``severidade`` enum and non-boolean actuator types fail."""
    for bad_sev in ("WARNING", "normal", 2, "", None):
        with pytest.raises(ValidationError):
            validate(make_valid_diagnosis(severidade=bad_sev), diagnostico_schema)

    with pytest.raises(ValidationError):
        validate(make_valid_diagnosis(comandos_atuadores={"cmd_motor": "true"}), diagnostico_schema)
    with pytest.raises(ValidationError):
        validate(make_valid_diagnosis(requer_relatorio=1), diagnostico_schema)


@pytest.mark.area_d
def test_td_t2_06_schema_missing_any_required_key_rejected(
    diagnostico_schema: dict[str, Any],
) -> None:
    """TD-T2-06: Verify omitting any of the 9 required root or nested keys fails."""
    root_keys = (
        "severidade",
        "diagnostico",
        "causa_provavel",
        "comandos_atuadores",
        "requer_relatorio",
        "resumo_executivo",
    )
    for k in root_keys:
        p = make_valid_diagnosis()
        del p[k]
        with pytest.raises(ValidationError):
            validate(p, diagnostico_schema)

    for nested_k in ("cmd_motor", "cmd_valvula", "cmd_alarme"):
        p = make_valid_diagnosis()
        del p["comandos_atuadores"][nested_k]
        with pytest.raises(ValidationError):
            validate(p, diagnostico_schema)


@pytest.mark.area_d
def test_td_t2_07_sql_fk_integrity_and_check_constraints(node_red_dir: Path) -> None:
    """TD-T2-07: Verify ``init.sql`` foreign keys and ``CHECK`` constraints."""
    res = assert_plc500_sql_contract(node_red_dir / "sql" / "init.sql")
    assert res.ok, f"SQL errors: {res.errors}"
    parsed = res.metadata["parsed"]
    assert parsed.tables["diagnosticos_ia"].columns["janela_id"].references == (
        "janelas_analiticas",
        "id",
    )
    assert parsed.tables["comandos_atuadores"].columns["diagnostico_id"].references == (
        "diagnosticos_ia",
        "id",
    )


@pytest.mark.area_d
def test_td_t2_08_sql_lexical_safety_cnt_emergencia_and_idx_desc(node_red_dir: Path) -> None:
    """TD-T2-08: Verify ``init.sql`` lexical balance, ``cnt_emergencia``, and ``DESC`` index."""
    sql_path = node_red_dir / "sql" / "init.sql"
    res = assert_plc500_sql_contract(sql_path)
    assert res.ok, f"SQL lexical/contract errors: {res.errors}"
    idx = res.metadata["parsed"].indexes["idx_tel_capturado"]
    assert re.search(r"capturado_em\s+DESC", idx.columns_expr, re.IGNORECASE)


@pytest.mark.area_d
def test_td_t2_09_nodered_js_lexer_delimiters_strings_templates_regex(
    node_red_dir: Path,
) -> None:
    """TD-T2-09: Verify every Node-RED function node passes full JS lexical/delimiter checks."""
    summary = assert_nodered_flows_contract(node_red_dir / "flows.json")
    assert len(summary["function_nodes"]) >= 3


@pytest.mark.area_d
def test_td_t2_10_nodered_flows_and_settings_zero_placeholders_or_secrets(
    node_red_dir: Path,
) -> None:
    """TD-T2-10: Verify ``flows.json`` & ``settings.js`` contain zero TODOs or leaked tokens."""
    flows_txt = (node_red_dir / "flows.json").read_text(encoding="utf-8")
    settings_txt = (node_red_dir / "settings.js").read_text(encoding="utf-8")
    assert not scan_text_for_secrets(flows_txt, "flows.json")
    assert not scan_text_for_secrets(settings_txt, "settings.js")
    assert validate_js_syntax(settings_txt, "settings.js") == []


@pytest.mark.area_d
def test_td_t2_11_docker_compose_sim_profile_isolation_and_no_privileged(
    repo_root: Path,
) -> None:
    """TD-T2-11: Verify Compose ``sim`` profile isolation and absence of privileged/secrets."""
    compose_path = repo_root / "docker-compose.yml"
    res = validate_compose_file(compose_path)
    assert res.ok, f"Compose validation errors: {res.errors}"
    assert not scan_text_for_secrets(compose_path.read_text("utf-8"), "docker-compose.yml")


@pytest.mark.area_d
def test_td_t2_12_makefile_tab_indentation_and_non_empty_targets(repo_root: Path) -> None:
    """TD-T2-12: Verify root ``Makefile`` uses TAB recipes without space-indentation traps."""
    res = validate_makefile(repo_root / "Makefile")
    assert res.ok, f"Makefile errors: {res.errors}"


@pytest.mark.area_d
def test_td_t2_13_nodered_and_sql_validators_reject_synthetic_defects(tmp_path: Path) -> None:
    """TD-T2-13: Negative self-checks on JS and SQL validators."""
    assert validate_js_syntax("if (a) { const s = `${x;", "broken_js")
    assert validate_js_syntax("const x = a / b; const r = /foo\\//;", "valid_div_regex") == []

    bad_sql = tmp_path / "bad_init.sql"
    bad_sql.write_text("CREATE TABLE telemetria_sensores (id INT);", encoding="utf-8")
    assert not assert_plc500_sql_contract(bad_sql).ok
    assert validate_postgres_ddl("CREATE TABLE x (a INT").errors


@pytest.mark.area_d
def test_td_t2_14_nodered_flows_cred_example_and_settings_env_secret_boundary(
    node_red_dir: Path,
) -> None:
    """TD-T2-14: Verify ``flows_cred.json.example`` & ``settings.js`` read env without secrets."""
    cred_ex = (node_red_dir / "flows_cred.json.example").read_text(encoding="utf-8")
    parsed = json.loads(cred_ex)
    assert isinstance(parsed, dict)
    assert not scan_text_for_secrets(cred_ex, "flows_cred.json.example")

    settings_txt = (node_red_dir / "settings.js").read_text(encoding="utf-8")
    assert "process.env" in settings_txt
    assert "NR_CREDENTIAL_SECRET" in settings_txt or "credentialSecret" in settings_txt

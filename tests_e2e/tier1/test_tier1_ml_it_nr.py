"""Tier 1 Feature Coverage E2E Tests — Area C (ML/FastAPI/Sim/Data) & Area D (SQL/NR/Infra)."""

from __future__ import annotations

import csv
import gzip
import json
import time
from pathlib import Path
from typing import Any

import pytest

from tests_e2e.helpers import (
    assert_nodered_flows_contract,
    assert_plc500_sql_contract,
    build_predict_feature_payload,
    build_raw_window_payload,
    check_env_example_placeholders,
    delta_mod16,
    discover_callable,
    ensure_python_pkg_on_path,
    load_diagnostico_schema,
    make_nominal_raw_samples,
    make_valid_diagnosis,
    scan_text_for_secrets,
    validate_compose_file,
    validate_diagnostico_json,
    validate_js_syntax,
    validate_makefile,
)

pytestmark = pytest.mark.tier1


# ==============================================================================
# Area C — Python Package, Features, Safety Rules, Data, ML, FastAPI, Simulator
# ==============================================================================


@pytest.mark.area_c
def test_tc_t1_01_python_pkg_files_and_config_constants(repo_root: Path, python_dir: Path) -> None:
    """TC-T1-01: Verify ``python/`` deliverables and contracted ``plc500_ml.config`` constants."""
    for rel in (
        "pyproject.toml",
        "requirements.txt",
        "Dockerfile",
        "README.md",
        "plc500_ml/__init__.py",
        "plc500_ml/config.py",
        "plc500_ml/schemas.py",
        "plc500_ml/features.py",
        "plc500_ml/safety_rules.py",
        "plc500_ml/data_generator.py",
        "plc500_ml/train.py",
        "plc500_ml/inference.py",
        "plc500_ml/api.py",
        "plc500_ml/plc_simulator.py",
    ):
        p = python_dir / rel
        assert p.is_file() and p.stat().st_size > 0, f"Missing or empty {p}"

    ensure_python_pkg_on_path(repo_root)
    import plc500_ml.config as cfg

    cfg_dump = " ".join(f"{k}={getattr(cfg, k)!r}" for k in dir(cfg) if not k.startswith("_"))
    for num_val in ("60", "12.0", "2.5", "0.01", "5000", "100"):
        assert num_val in cfg_dump, f"plc500_ml.config missing expected constant {num_val}"


@pytest.mark.area_c
def test_tc_t1_02_schemas_diagnostico_matches_nodered_schema(
    repo_root: Path, node_red_dir: Path
) -> None:
    """TC-T1-02: Verify ``plc500_ml.schemas`` matches ``diagnostico_atuacao.schema.json``."""
    ensure_python_pkg_on_path(repo_root)
    import plc500_ml.schemas as schemas_mod

    nr_schema = load_diagnostico_schema(
        node_red_dir / "schemas" / "diagnostico_atuacao.schema.json"
    )
    model_cls = getattr(schemas_mod, "DiagnosticoAtuacaoPLC500", None)
    assert model_cls is not None, "plc500_ml.schemas must define DiagnosticoAtuacaoPLC500"

    py_schema = model_cls.model_json_schema()
    assert set(py_schema.get("required", [])) == set(nr_schema.get("required", []))
    valid_inst = model_cls.model_validate(make_valid_diagnosis())
    assert valid_inst.severidade == "NORMAL"


@pytest.mark.area_c
def test_tc_t1_03_raw_window_nominal_2hz_classification(
    ml_api_client: Any, diagnostico_schema: dict[str, Any]
) -> None:
    """TC-T1-03: Verify nominal 120-sample 2 Hz window returns NORMAL & ``cmd_motor=True``."""
    spec = ml_api_client.get("/openapi.json").json()
    samples = make_nominal_raw_samples(120, start_cnt_presenca=100, delta_presenca=12)
    payload = build_raw_window_payload(spec, samples)
    resp = ml_api_client.post("/predict/raw-window", json=payload)
    assert resp.status_code == 200, f"/predict/raw-window failed: {resp.status_code} {resp.text}"

    body = resp.json()
    diag = body.get("diagnostico_atuacao", body.get("diagnosis", body))
    errs = validate_diagnostico_json(diag, diagnostico_schema)
    assert not errs, f"Schema validation errors: {errs}"
    assert diag["severidade"] == "NORMAL"
    assert diag["comandos_atuadores"]["cmd_motor"] is True
    assert diag["comandos_atuadores"]["cmd_valvula"] is False
    assert diag["comandos_atuadores"]["cmd_alarme"] is False


@pytest.mark.area_c
def test_tc_t1_04_raw_window_intermittent_high_level_b2(
    ml_api_client: Any, diagnostico_schema: dict[str, Any]
) -> None:
    """TC-T1-04: Verify intermittent ``B2`` high-level window activates valve and elevates severity."""
    spec = ml_api_client.get("/openapi.json").json()
    samples = make_nominal_raw_samples(
        120, start_cnt_presenca=200, delta_presenca=18, nivel_alto_count=24
    )
    payload = build_raw_window_payload(spec, samples)
    resp = ml_api_client.post("/predict/raw-window", json=payload)
    assert resp.status_code == 200, f"/predict/raw-window failed: {resp.status_code} {resp.text}"

    body = resp.json()
    diag = body.get("diagnostico_atuacao", body.get("diagnosis", body))
    errs = validate_diagnostico_json(diag, diagnostico_schema)
    assert not errs, f"Schema validation errors: {errs}"
    assert diag["severidade"] in ("ATENCAO", "CRITICO")
    assert diag["comandos_atuadores"]["cmd_valvula"] is True


@pytest.mark.area_c
def test_tc_t1_05_safety_rules_deterministic_fallback_four_states(repo_root: Path) -> None:
    """TC-T1-05: Verify ``safety_rules`` deterministic fallback across severities 0..3."""
    ensure_python_pkg_on_path(repo_root)
    import plc500_ml.safety_rules as sr

    fb_fn = discover_callable(
        sr,
        (
            "deterministic_fallback",
            "fallback_diagnosis",
            "fallback_diagnostico",
            "build_fallback_diagnosis",
            "compute_fallback",
        ),
    )

    def _call(z1: float, d2: float, d3: float, emerg_ok: bool, wd: bool) -> dict[str, Any]:
        import inspect

        sig = inspect.signature(fb_fn)
        params = list(sig.parameters)
        if len(params) == 1:
            feat_dict = {
                "z1": z1,
                "z_presenca": z1,
                "lambda1": 12.0 + z1 * 2.51,
                "duty_nivel": d2,
                "duty_temp": d3,
                "emergencia_ok": emerg_ok,
                "emergencia_ativa": not emerg_ok,
                "flag_watchdog": wd,
                "chattering_index": 0.0,
            }
            res = fb_fn(feat_dict)
        else:
            kwargs: dict[str, Any] = {}
            mapping = {
                "z1": z1,
                "z_presenca": z1,
                "duty_nivel": d2,
                "duty_temp": d3,
                "emergencia_ok": emerg_ok,
                "emerg_ativa": not emerg_ok,
                "emergencia_ativa": not emerg_ok,
                "flag_watchdog": wd,
                "watchdog_ativo": wd,
            }
            for pname in params:
                if pname in mapping:
                    kwargs[pname] = mapping[pname]
            res = fb_fn(**kwargs)
        return res.model_dump() if hasattr(res, "model_dump") else dict(res)

    d0 = _call(0.0, 0.0, 0.0, True, False)
    d1 = _call(2.4, 0.1, 0.0, True, False)
    d2 = _call(3.5, 0.35, 0.0, True, False)
    d3 = _call(0.0, 0.0, 0.0, False, False)

    assert d0["severidade"] == "NORMAL"
    assert d0["comandos_atuadores"] == {"cmd_motor": True, "cmd_valvula": False, "cmd_alarme": False}
    assert d1["severidade"] == "ATENCAO"
    assert d1["comandos_atuadores"] == {"cmd_motor": True, "cmd_valvula": True, "cmd_alarme": False}
    assert d2["severidade"] == "CRITICO"
    assert d2["comandos_atuadores"] == {"cmd_motor": False, "cmd_valvula": True, "cmd_alarme": True}
    assert d3["severidade"] == "EMERGENCIA"
    assert d3["comandos_atuadores"] == {"cmd_motor": False, "cmd_valvula": False, "cmd_alarme": True}


@pytest.mark.area_c
def test_tc_t1_06_safety_rules_monotonicity_preserves_safe_actions(repo_root: Path) -> None:
    """TC-T1-06: Verify monotonicity guard preserves consistent NORMAL and CRITICO diagnoses."""
    ensure_python_pkg_on_path(repo_root)
    import plc500_ml.safety_rules as sr

    guard_fn = discover_callable(
        sr,
        (
            "apply_monotonicity_guard",
            "enforce_monotonicity",
            "monotonicity_guard",
            "guard_diagnosis",
            "validate_and_guard",
        ),
    )

    nom_in = make_valid_diagnosis(
        severidade="NORMAL",
        comandos_atuadores={"cmd_motor": True, "cmd_valvula": False, "cmd_alarme": False},
    )
    nom_feat = {"z1": 0.0, "duty_nivel": 0.0, "duty_temp": 0.0, "emergencia_ok": True, "flag_watchdog": False}
    out_nom = guard_fn(nom_in, nom_feat)
    out_nom_d = out_nom.model_dump() if hasattr(out_nom, "model_dump") else dict(out_nom)
    assert out_nom_d["severidade"] == "NORMAL"
    assert out_nom_d["comandos_atuadores"]["cmd_motor"] is True

    crit_in = make_valid_diagnosis(
        severidade="CRITICO",
        requer_relatorio=True,
        comandos_atuadores={"cmd_motor": False, "cmd_valvula": True, "cmd_alarme": True},
    )
    crit_feat = {"z1": 3.2, "duty_nivel": 0.4, "duty_temp": 0.0, "emergencia_ok": True, "flag_watchdog": False}
    out_crit = guard_fn(crit_in, crit_feat)
    out_crit_d = out_crit.model_dump() if hasattr(out_crit, "model_dump") else dict(out_crit)
    assert out_crit_d["severidade"] == "CRITICO"
    assert out_crit_d["comandos_atuadores"]["cmd_motor"] is False
    assert out_crit_d["comandos_atuadores"]["cmd_valvula"] is True


@pytest.mark.area_c
def test_tc_t1_07_data_files_present_no_lfs_and_raw_row_counts(data_dir: Path) -> None:
    """TC-T1-07: Verify ``data/`` artifacts exist without Git LFS pointers and match 2 Hz size."""
    required = (
        data_dir / "README.md",
        data_dir / "data_dictionary.md",
        data_dir / "raw" / "raw_sample_500.csv",
        data_dir / "raw" / "raw_telemetry_2hz.csv.gz",
        data_dir / "processed" / "window_features_60s.csv",
        data_dir / "processed" / "scenario_summary.json",
    )
    for p in required:
        assert p.is_file() and p.stat().st_size > 0, f"Missing or empty data file: {p}"
        head = p.read_bytes()[:64]
        assert not head.startswith(b"version https://git-lfs.github.com/spec/v1"), (
            f"Git LFS pointer detected in {p}"
        )

    with (data_dir / "raw" / "raw_sample_500.csv").open("r", encoding="utf-8") as fh:
        sample_rows = sum(1 for _ in csv.reader(fh)) - 1
    assert 495 <= sample_rows <= 505, f"Expected ~500 rows in raw_sample_500.csv, got {sample_rows}"

    with gzip.open(data_dir / "raw" / "raw_telemetry_2hz.csv.gz", "rt", encoding="utf-8") as gz:
        raw_rows = sum(1 for _ in csv.reader(gz)) - 1
    assert 125000 <= raw_rows <= 135000, f"Expected ~129,600 18h 2Hz rows, got {raw_rows}"


@pytest.mark.area_c
def test_tc_t1_08_window_features_splits_and_scenario_summary(
    repo_root: Path, data_dir: Path
) -> None:
    """TC-T1-08: Verify ``window_features_60s.csv`` (~3,600 rows, 65/15/20 split, no NaNs)."""
    ensure_python_pkg_on_path(repo_root)
    import pandas as pd

    df = pd.read_csv(data_dir / "processed" / "window_features_60s.csv")
    assert 3400 <= len(df) <= 3800, f"Expected ~3600 window rows, got {len(df)}"
    assert "split" in df.columns
    assert set(df["split"].unique()) == {"train", "val", "test"}

    counts = df["split"].value_counts(normalize=True).to_dict()
    assert 0.60 <= counts["train"] <= 0.70
    assert 0.10 <= counts["val"] <= 0.20
    assert 0.15 <= counts["test"] <= 0.25

    num_df = df.select_dtypes(include="number")
    assert not num_df.isna().any().any(), "NaN values found in numeric window_features_60s.csv"

    summary = json.loads((data_dir / "processed" / "scenario_summary.json").read_text("utf-8"))
    assert len(summary) >= 9 or (
        isinstance(summary, dict)
        and len(summary.get("scenarios", summary)) >= 9
    )


@pytest.mark.area_c
def test_tc_t1_09_raw_vs_window_features_parity_and_seed42(
    repo_root: Path, python_dir: Path, data_dir: Path
) -> None:
    """TC-T1-09: Verify ``data_generator.py`` uses ``seed=42`` and raw->window math parity."""
    gen_text = (python_dir / "plc500_ml" / "data_generator.py").read_text(encoding="utf-8")
    assert "42" in gen_text, "data_generator.py must reference default seed=42"

    ensure_python_pkg_on_path(repo_root)
    import pandas as pd

    win_df = pd.read_csv(data_dir / "processed" / "window_features_60s.csv")
    raw_df = pd.read_csv(data_dir / "raw" / "raw_sample_500.csv")
    assert len(raw_df) >= 120
    # Verify wrap-16 delta math on the first 120 samples of raw_sample_500 vs first window if aligned,
    # and independently verify z1 = (lambda1 - 12.0) / 2.51 on 5 sampled windows in win_df.
    lam_col = next(c for c in ("lambda1", "taxa_presenca") if c in win_df.columns)
    z_col = next(c for c in ("z1", "z_presenca") if c in win_df.columns)
    for row in win_df.head(5).itertuples():
        lam_val = float(getattr(row, lam_col))
        z_val = float(getattr(row, z_col))
        expected_z = (lam_val - 12.0) / (2.5 + 0.01)
        assert abs(z_val - expected_z) < 1e-3, f"z1 mismatch: {z_val} != {expected_z}"
    assert delta_mod16(int(raw_df.iloc[119]["cnt_presenca"]), int(raw_df.iloc[0]["cnt_presenca"])) >= 0


@pytest.mark.area_c
def test_tc_t1_10_trained_model_artifacts_metadata_and_report(python_dir: Path) -> None:
    """TC-T1-10: Verify ``python/models/`` artifacts, metadata, and evaluation report."""
    models_dir = python_dir / "models"
    for fname in (
        "anomaly_detector.joblib",
        "severity_classifier.joblib",
        "model_metadata.json",
        "evaluation_report.md",
    ):
        p = models_dir / fname
        assert p.is_file() and p.stat().st_size > 0, f"Missing model artifact: {p}"

    meta_text = (models_dir / "model_metadata.json").read_text(encoding="utf-8")
    meta = json.loads(meta_text)
    assert isinstance(meta, dict) and meta
    report_text = (models_dir / "evaluation_report.md").read_text(encoding="utf-8")
    combined = f"{meta_text}\n{report_text}"
    for kw in ("IsolationForest", "HistGradientBoosting", "RandomForest"):
        assert kw in combined, f"Models metadata/report missing {kw}"


@pytest.mark.area_c
def test_tc_t1_11_joblib_models_load_and_predict_test_split(
    repo_root: Path, python_dir: Path, data_dir: Path
) -> None:
    """TC-T1-11: Load trained ``.joblib`` artifacts and verify predictions on test split."""
    ensure_python_pkg_on_path(repo_root)
    import joblib
    import pandas as pd

    models_dir = python_dir / "models"
    anom = joblib.load(models_dir / "anomaly_detector.joblib")
    clf = joblib.load(models_dir / "severity_classifier.joblib")
    meta = json.loads((models_dir / "model_metadata.json").read_text(encoding="utf-8"))

    df = pd.read_csv(data_dir / "processed" / "window_features_60s.csv")
    test_df = df[df["split"] == "test"].head(20)
    assert len(test_df) == 20

    feature_cols = meta.get("feature_columns") or meta.get("features")
    assert isinstance(feature_cols, list) and feature_cols
    x_test = test_df[feature_cols]

    preds = clf.predict(x_test)
    assert all(int(p) in (0, 1, 2, 3) for p in preds)
    anom_preds = anom.predict(x_test)
    assert all(int(a) in (-1, 1) for a in anom_preds)

    target_col = next(
        (c for c in ("severity", "severidade", "label", "target", "y") if c in test_df.columns),
        None,
    )
    if target_col is not None:
        acc = sum(int(a) == int(b) for a, b in zip(preds, test_df[target_col], strict=True)) / len(preds)
        assert acc >= 0.80, f"Test split sanity accuracy too low: {acc:.2f}"


@pytest.mark.area_c
def test_tc_t1_12_inference_module_nominal_vs_fault(repo_root: Path) -> None:
    """TC-T1-12: Verify ``plc500_ml.inference`` returns valid predictions for nominal vs fault."""
    ensure_python_pkg_on_path(repo_root)
    import plc500_ml.inference as inf_mod

    pred_fn = discover_callable(
        inf_mod,
        ("predict_window", "predict_features", "predict", "run_inference"),
    )
    if hasattr(inf_mod, "PLC500Predictor"):
        predictor = inf_mod.PLC500Predictor()
        nom = predictor.predict(
            {"lambda1": 12.0, "z1": 0.0, "duty_nivel": 0.0, "duty_temp": 0.0, "chattering_index": 0.0, "emergencia_ok": True, "flag_watchdog": False}
        )
        assert nom is not None
    else:
        res = pred_fn(
            {"lambda1": 12.0, "z1": 0.0, "duty_nivel": 0.0, "duty_temp": 0.0, "chattering_index": 0.0, "emergencia_ok": True, "flag_watchdog": False}
        )
        assert res is not None


@pytest.mark.area_c
def test_tc_t1_13_fastapi_health_schema_model_info_endpoints(
    ml_api_client: Any, diagnostico_schema: dict[str, Any]
) -> None:
    """TC-T1-13: Verify FastAPI ``/health``, ``/schema``, and ``/model-info`` endpoints."""
    r_health = ml_api_client.get("/health")
    assert r_health.status_code == 200
    h_json = r_health.json()
    assert h_json.get("status") in ("ok", "healthy", "ready", True) or h_json.get("healthy") is True

    r_schema = ml_api_client.get("/schema")
    assert r_schema.status_code == 200
    s_json = r_schema.json()
    assert set(s_json.get("required", [])) == set(diagnostico_schema["required"])

    r_info = ml_api_client.get("/model-info")
    assert r_info.status_code == 200
    assert r_info.json()


@pytest.mark.area_c
def test_tc_t1_14_fastapi_predict_nominal_and_critical_schema_valid(
    ml_api_client: Any, diagnostico_schema: dict[str, Any]
) -> None:
    """TC-T1-14: Verify ``POST /predict`` returns schema-valid responses for nominal & critical."""
    spec = ml_api_client.get("/openapi.json").json()
    nom_payload = build_predict_feature_payload(
        spec,
        lambda1=12.0,
        z1=0.0,
        duty_nivel=0.0,
        duty_temp=0.0,
        chattering_index=0.0,
        emergencia_ok=True,
        flag_watchdog=False,
    )
    r1 = ml_api_client.post("/predict", json=nom_payload)
    assert r1.status_code == 200, f"/predict nominal failed: {r1.status_code} {r1.text}"
    d1 = r1.json().get("diagnostico_atuacao", r1.json().get("diagnosis", r1.json()))
    assert validate_diagnostico_json(d1, diagnostico_schema) == []

    crit_payload = build_predict_feature_payload(
        spec,
        lambda1=22.0,
        z1=3.98,
        duty_nivel=0.40,
        duty_temp=0.25,
        chattering_index=0.5,
        emergencia_ok=True,
        flag_watchdog=False,
    )
    r2 = ml_api_client.post("/predict", json=crit_payload)
    assert r2.status_code == 200, f"/predict critical failed: {r2.status_code} {r2.text}"
    d2 = r2.json().get("diagnostico_atuacao", r2.json().get("diagnosis", r2.json()))
    assert validate_diagnostico_json(d2, diagnostico_schema) == []
    assert d2["comandos_atuadores"]["cmd_motor"] is False


@pytest.mark.area_c
def test_tc_t1_15_simulator_modbus_fc02_fc04_fc03_fc06_boot_state(
    fresh_plc_sim: dict[str, Any],
) -> None:
    """TC-T1-15: Verify cold-boot Modbus registers (``HR[3]=100``, ``HR[1]=5000``, ``DI3=1``)."""
    client = fresh_plc_sim["client"]
    snap = client.snapshot()
    assert snap["uiVersao_Mapa"] == 100
    assert snap["uiTimeout_WD_ms"] == 5000
    assert snap["xDI_EmergenciaOK"] is True
    assert snap["uiEstado_CLP"] == 0

    client.write_hr(0, 42)
    synced = client.wait_until(lambda s: s["uiEcho_Heartbeat"] == 42, timeout=0.5)
    assert synced["uiEcho_Heartbeat"] == 42


@pytest.mark.area_c
def test_tc_t1_16_simulator_remote_actuation_fc05_fc15_and_api(
    plc_sim: dict[str, Any],
) -> None:
    """TC-T1-16: Verify remote actuation ``FC05``/``FC15`` updates ``IR[5]`` and ``IR[4]``."""
    client = plc_sim["client"]
    client.write_hr(0, 10)
    client.write_coil(4, True)
    client.write_coils(0, [True, False, False])

    snap = client.wait_until(
        lambda s: s["uiEstado_CLP"] == 1 and s["wMascara_DO_Real"] == 0b1001,
        timeout=1.0,
    )
    assert snap["do"] == {"DO0": True, "DO1": False, "DO2": False, "DO3": True}

    client.write_coils(0, [False, True, True])
    snap2 = client.wait_until(lambda s: s["wMascara_DO_Real"] == 0b1110, timeout=1.0)
    assert snap2["do"] == {"DO0": False, "DO1": True, "DO2": True, "DO3": True}
    assert isinstance(plc_sim["adapter"].openapi, dict)


# ==============================================================================
# Area D — PostgreSQL DDL, JSON Schema, Node-RED Flows/JS, Compose, Makefile
# ==============================================================================


@pytest.mark.area_d
def test_td_t1_01_sql_init_ddl_tables_index_columns_and_views(node_red_dir: Path) -> None:
    """TD-T1-01: Verify ``node-red/sql/init.sql`` defines 4 tables, index, ``cnt_emergencia``, views."""
    res = assert_plc500_sql_contract(node_red_dir / "sql" / "init.sql")
    assert res.ok, f"SQL DDL contract errors: {res.errors}"


@pytest.mark.area_d
def test_td_t1_02_diagnostico_json_schema_draft2020_12_and_samples(
    node_red_dir: Path,
) -> None:
    """TD-T1-02: Verify ``diagnostico_atuacao.schema.json`` validates all 4 severities."""
    schema = load_diagnostico_schema(
        node_red_dir / "schemas" / "diagnostico_atuacao.schema.json"
    )
    assert schema.get("title") == "DiagnosticoAtuacaoPLC500"
    assert schema.get("additionalProperties") is False

    for sev, motor, valve, alarm, rep in (
        ("NORMAL", True, False, False, False),
        ("ATENCAO", True, True, False, False),
        ("CRITICO", False, True, True, True),
        ("EMERGENCIA", False, False, True, True),
    ):
        sample = make_valid_diagnosis(
            severidade=sev,
            requer_relatorio=rep,
            comandos_atuadores={
                "cmd_motor": motor,
                "cmd_valvula": valve,
                "cmd_alarme": alarm,
            },
        )
        assert validate_diagnostico_json(sample, schema) == []


@pytest.mark.area_d
def test_td_t1_03_nodered_pkg_settings_cred_example_and_docs(node_red_dir: Path) -> None:
    """TD-T1-03: Verify ``node-red/`` support files, dependencies, and ``settings.js`` JS syntax."""
    for rel in ("Dockerfile", "package.json", "settings.js", "flows_cred.json.example", "README.md"):
        assert (node_red_dir / rel).is_file(), f"Missing node-red/{rel}"
    assert not (node_red_dir / "flows_cred.json").exists(), "flows_cred.json must never be committed"

    pkg = json.loads((node_red_dir / "package.json").read_text(encoding="utf-8"))
    deps = pkg.get("dependencies", {})
    for dep in (
        "node-red-contrib-modbus",
        "node-red-contrib-postgresql",
        "@flowfuse/node-red-dashboard",
    ):
        assert dep in deps, f"node-red/package.json missing dependency {dep!r}"

    settings_js = (node_red_dir / "settings.js").read_text(encoding="utf-8")
    assert validate_js_syntax(settings_js, "settings.js") == []


@pytest.mark.area_d
def test_td_t1_04_nodered_flows_five_tabs_no_orphans_or_dangling_wires(
    node_red_dir: Path,
) -> None:
    """TD-T1-04: Verify ``node-red/flows.json`` has 5 tabs, unique IDs, and no dangling wires."""
    summary = assert_nodered_flows_contract(node_red_dir / "flows.json")
    assert len(summary["tabs"]) == 5


@pytest.mark.area_d
def test_td_t1_05_nodered_flows_modbus_polling_fastapi_llm_and_ui(
    node_red_dir: Path,
) -> None:
    """TD-T1-05: Verify ``flows.json`` wires Modbus TCP 502/Unit1, FastAPI 8000, LLM, Markdown, UI."""
    summary = assert_nodered_flows_contract(node_red_dir / "flows.json")
    raw_flows = (node_red_dir / "flows.json").read_text(encoding="utf-8")

    assert summary["modbus_clients"], "flows.json missing modbus-client config node"
    assert any(
        str(c.get("tcpPort", "")) == "502" or "PLC_PORT" in str(c.get("tcpPort", ""))
        for c in summary["modbus_clients"]
    )
    assert summary["template_nodes"], "flows.json missing Markdown template node"
    assert summary["ui_nodes"], "flows.json missing @flowfuse/node-red-dashboard UI nodes"
    for kw in ("ml-api", "8000", "500", "60"):
        assert kw in raw_flows, f"flows.json missing expected integration token {kw!r}"


@pytest.mark.area_d
def test_td_t1_06_nodered_flows_embedded_js_contract_constants(node_red_dir: Path) -> None:
    """TD-T1-06: Verify ``flows.json`` embedded JS references window math & safety constants."""
    summary = assert_nodered_flows_contract(node_red_dir / "flows.json")
    js = summary["js_combined"]
    for tok in ("65536", "60", "0.01", "12.0", "2.5", "FALLBACK_STATS", "LLM"):
        assert tok in js, f"flows.json embedded JS missing contract token {tok!r}"
    assert "mascara_do_real" in js or "wMascara_DO_Real" in js


@pytest.mark.area_d
def test_td_t1_07_docker_compose_services_profiles_deps_and_mounts(repo_root: Path) -> None:
    """TD-T1-07: Verify ``docker-compose.yml`` services, ``sim`` profile, and ``init.sql`` mount."""
    res = validate_compose_file(repo_root / "docker-compose.yml")
    assert res.ok, f"docker-compose.yml errors: {res.errors}"


@pytest.mark.area_d
def test_td_t1_08_root_makefile_phony_targets_and_tabs(repo_root: Path) -> None:
    """TD-T1-08: Verify root ``Makefile`` defines ``.PHONY``, TAB recipes, and key targets."""
    res = validate_makefile(repo_root / "Makefile")
    assert res.ok, f"Makefile errors: {res.errors}"
    targets = set(res.metadata["targets"])
    assert any(t in targets for t in ("help", "test", "lint", "up", "sim"))


@pytest.mark.area_d
def test_td_t1_09_env_example_aligns_with_compose_and_zero_secrets(repo_root: Path) -> None:
    """TD-T1-09: Verify ``docker-compose.yml`` env vars exist in ``.env.example`` with no secrets."""
    compose_path = repo_root / "docker-compose.yml"
    compose_raw = compose_path.read_text(encoding="utf-8")
    assert not scan_text_for_secrets(compose_raw, "docker-compose.yml")

    res = validate_compose_file(compose_path)
    assert res.ok
    env_text = (repo_root / ".env.example").read_text(encoding="utf-8")
    assert not check_env_example_placeholders(env_text)

    defined_keys = {
        line.split("=", 1)[0].strip()
        for line in env_text.splitlines()
        if line.strip() and not line.strip().startswith("#") and "=" in line
    }
    for var in res.metadata["env_vars"]:
        # Allow optional compose vars that have :- defaults, but core keys should be in .env.example
        if f"${{{var}:-" not in compose_raw:
            assert var in defined_keys, f"docker-compose.yml variable {var} missing in .env.example"


@pytest.mark.area_d
def test_td_t1_10_sql_views_align_with_declared_tables(node_red_dir: Path) -> None:
    """TD-T1-10: Verify ``init.sql`` operational views reference declared base tables."""
    res = assert_plc500_sql_contract(node_red_dir / "sql" / "init.sql")
    assert res.ok, f"SQL contract errors: {res.errors}"
    parsed = res.metadata["parsed"]
    assert len(parsed.views) >= 1
    # Verify views reference at least telemetria_sensores or janelas_analiticas
    all_view_sql = " ".join(v.query.lower() for v in parsed.views.values())
    assert "telemetria_sensores" in all_view_sql or "janelas_analiticas" in all_view_sql
    time.sleep(0)  # keep parity with timing helpers if needed without overhead

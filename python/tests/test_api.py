"""FastAPI endpoint, JSON Schema, fallback, and adversarial safety tests."""

from __future__ import annotations

from pathlib import Path

import jsonschema
import numpy as np
from fastapi.testclient import TestClient

from plc500_ml.api import app
from plc500_ml.inference import PLC500InferenceEngine
from plc500_ml.schemas import DIAGNOSTICO_ATUACAO_SCHEMA


class _UnsafeMockClassifier:
    """Adversarial mock always predicting NORMAL with 100% confidence."""

    def predict(self, X: np.ndarray) -> np.ndarray:
        return np.zeros(len(X), dtype=int)

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        out = np.zeros((len(X), 4), dtype=float)
        out[:, 0] = 1.0
        return out


def test_health_schema_and_model_info_endpoints() -> None:
    with TestClient(app) as client:
        r_health = client.get("/health")
        assert r_health.status_code == 200
        h = r_health.json()
        assert h["status"] == "ok"
        assert h["models_loaded"] is True
        assert h["fallback_mode"] is False
        assert h["map_version"] == 100

        r_schema = client.get("/schema")
        assert r_schema.status_code == 200
        s = r_schema.json()
        assert s == DIAGNOSTICO_ATUACAO_SCHEMA
        assert s["additionalProperties"] is False

        r_info = client.get("/model-info")
        assert r_info.status_code == 200
        info = r_info.json()
        assert info["models_loaded"] is True
        assert len(info["feature_names"]) == 10


def test_predict_summary_and_nodered_payload_compatibility() -> None:
    with TestClient(app) as client:
        # Nominal Node-RED msg.payload with extra fields (ignored cleanly)
        nodered_payload = {
            "janela_inicio": "2026-01-15T00:00:00Z",
            "janela_fim": "2026-01-15T00:01:00Z",
            "amostras_k": 120,
            "taxa_presenca_min": 12.0,
            "duty_nivel": 0.0,
            "duty_sobretemp": 0.0,
            "zscore_presenca": 0.0,
            "di_emergencia_ok": True,
            "flag_emerg_travada": False,
            "flag_watchdog": False,
            "severidade_estatistica": 0,
            "fallback_comandos": {"cmd_motor": True, "cmd_valvula": False, "cmd_alarme": False},
            "acionar_llm": False,
        }
        res_nom = client.post("/predict", json=nodered_payload)
        assert res_nom.status_code == 200
        body_nom = res_nom.json()
        assert body_nom["origem_decisao"] == "ML_FASTAPI"
        assert body_nom["severidade_codigo"] == 0
        jsonschema.validate(body_nom["diagnostico_atuacao"], DIAGNOSTICO_ATUACAO_SCHEMA)

        # Overtemperature -> motor must be False, valvula & alarme True
        res_temp = client.post(
            "/predict",
            json={
                "taxa_presenca_min": 0.0,
                "duty_nivel": 0.0,
                "duty_sobretemp": 0.5,
                "di_sobretemp": True,
            },
        )
        assert res_temp.status_code == 200
        b_temp = res_temp.json()
        assert b_temp["severidade_codigo"] == 2
        assert b_temp["diagnostico_atuacao"]["comandos_atuadores"] == {
            "cmd_motor": False,
            "cmd_valvula": True,
            "cmd_alarme": True,
        }
        jsonschema.validate(b_temp["diagnostico_atuacao"], DIAGNOSTICO_ATUACAO_SCHEMA)

        # Emergency -> motor=False, valvula=False, alarme=True
        res_emg = client.post(
            "/predict",
            json={
                "taxa_presenca_min": 0.0,
                "duty_nivel": 0.0,
                "duty_sobretemp": 0.0,
                "di_emergencia_ok": False,
                "flag_emerg_travada": True,
            },
        )
        assert res_emg.status_code == 200
        b_emg = res_emg.json()
        assert b_emg["severidade_codigo"] == 3
        assert b_emg["diagnostico_atuacao"]["comandos_atuadores"] == {
            "cmd_motor": False,
            "cmd_valvula": False,
            "cmd_alarme": True,
        }

        # Invalid duty > 1.0 -> 422
        res_bad = client.post(
            "/predict",
            json={"taxa_presenca_min": 12.0, "duty_nivel": 1.5, "duty_sobretemp": 0.0},
        )
        assert res_bad.status_code == 422


def test_predict_raw_window_wraparound_chattering_minimal_and_empty() -> None:
    with TestClient(app) as client:
        # 120 samples crossing 65535 -> 0 with nominal 12 pulses
        cnt = 65530
        samples = []
        for i in range(120):
            pulse = i > 0 and (i % 10 == 0)
            if pulse:
                cnt = (cnt + 1) % 65536
            samples.append(
                {
                    "di_presenca": pulse,
                    "di_nivel_alto": False,
                    "di_sobretemp": False,
                    "di_emergencia_ok": True,
                    "cnt_presenca": cnt,
                }
            )
        res_raw = client.post("/predict/raw-window", json={"samples": samples})
        assert res_raw.status_code == 200
        b = res_raw.json()
        assert b["severidade_codigo"] == 0
        assert b["features"]["delta_cnt_presenca"] == 11
        jsonschema.validate(b["diagnostico_atuacao"], DIAGNOSTICO_ATUACAO_SCHEMA)

        # Chattering burst (rapid edges + 35 pulses)
        cnt_chat = 100
        chat_samples = []
        for i in range(120):
            if i % 3 == 0:
                cnt_chat += 1
            chat_samples.append(
                {
                    "di_presenca": (i % 2 == 0),
                    "di_nivel_alto": False,
                    "di_sobretemp": False,
                    "di_emergencia_ok": True,
                    "cnt_presenca": cnt_chat,
                }
            )
        res_chat = client.post("/predict/raw-window", json={"samples": chat_samples})
        assert res_chat.status_code == 200
        assert res_chat.json()["severidade_codigo"] == 2
        assert res_chat.json()["diagnostico_atuacao"]["comandos_atuadores"]["cmd_motor"] is False

        # Minimal 1-sample window
        res_one = client.post(
            "/predict/raw-window",
            json={
                "samples": [
                    {
                        "di_presenca": False,
                        "di_nivel_alto": False,
                        "di_sobretemp": False,
                        "di_emergencia_ok": True,
                        "cnt_presenca": 42,
                    }
                ]
            },
        )
        assert res_one.status_code == 200

        # Empty window -> 422
        res_empty = client.post("/predict/raw-window", json={"samples": []})
        assert res_empty.status_code == 422


def test_missing_model_deterministic_fallback_and_adversarial_guard(tmp_path: Path) -> None:
    fallback_engine = PLC500InferenceEngine(models_dir=tmp_path)
    assert fallback_engine.models_loaded is False

    app.state.engine = fallback_engine
    try:
        client = TestClient(app)
        h = client.get("/health").json()
        assert h["status"] == "degraded"
        assert h["models_loaded"] is False
        assert h["fallback_mode"] is True

        r = client.post(
            "/predict",
            json={"taxa_presenca_min": 0.0, "duty_nivel": 0.5, "duty_sobretemp": 0.0},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["origem_decisao"] == "FALLBACK_STATS"
        assert body["severidade_codigo"] == 2
        assert body["diagnostico_atuacao"]["comandos_atuadores"]["cmd_motor"] is False
        jsonschema.validate(body["diagnostico_atuacao"], DIAGNOSTICO_ATUACAO_SCHEMA)
    finally:
        app.state.engine = PLC500InferenceEngine()

    # Adversarial classifier downgrade test on real engine
    adv_engine = PLC500InferenceEngine()
    adv_engine.severity_classifier = _UnsafeMockClassifier()
    app.state.engine = adv_engine
    try:
        client = TestClient(app)
        for payload in (
            {"taxa_presenca_min": 12.0, "duty_nivel": 0.0, "duty_sobretemp": 0.2},
            {"taxa_presenca_min": 12.0, "duty_nivel": 0.4, "duty_sobretemp": 0.0},
            {
                "taxa_presenca_min": 12.0,
                "duty_nivel": 0.0,
                "duty_sobretemp": 0.0,
                "flag_watchdog": True,
            },
            {
                "taxa_presenca_min": 12.0,
                "duty_nivel": 0.0,
                "duty_sobretemp": 0.0,
                "di_emergencia_ok": False,
            },
        ):
            r = client.post("/predict", json=payload)
            assert r.status_code == 200
            b = r.json()
            assert b["monotonicity_applied"] is True
            assert b["severidade_codigo"] >= 2
            assert b["diagnostico_atuacao"]["comandos_atuadores"]["cmd_motor"] is False
            jsonschema.validate(b["diagnostico_atuacao"], DIAGNOSTICO_ATUACAO_SCHEMA)
    finally:
        app.state.engine = PLC500InferenceEngine()

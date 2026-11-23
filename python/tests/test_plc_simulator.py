"""Unit and Modbus TCP protocol tests (CT-01..CT-09) for PLC500 simulator."""

from __future__ import annotations

from fastapi.testclient import TestClient

from plc500_ml.config import (
    ESTADO_ALERTA,
    ESTADO_EMERGENCIA_TRAV,
    ESTADO_INIT,
    ESTADO_MODO_DEGRADADO,
    ESTADO_NORMAL_AUTO,
    INTERTRAV_EMERGENCIA,
    INTERTRAV_NENHUM,
    INTERTRAV_SOBRETEMP,
    INTERTRAV_WATCHDOG,
    MAP_VERSION,
    WD_DEFAULT_MS,
)
from plc500_ml.plc_simulator import (
    PLC500ModbusSimulator,
    PLC500StateCore,
    create_control_app,
)


def test_ct01_debounce_50ms_and_wrap16_counters() -> None:
    core = PLC500StateCore()
    core.step(now=0.0)
    assert core.uiVersao_Mapa == MAP_VERSION
    assert core.uiTimeout_WD_ms == WD_DEFAULT_MS
    assert core.xDI_EmergenciaOK is True
    assert core.uiEstado_CLP == ESTADO_INIT

    # Sub-50 ms contact bounce (25 ms) on DI0 -> rejected
    core.set_raw_channel(0, True, now=1.0)
    core.step(now=1.010)
    core.step(now=1.025)
    assert core.xDI_Presenca is False
    assert core.uiCnt_Presenca == 0
    core.set_raw_channel(0, False, now=1.026)
    core.step(now=1.026)
    assert core.uiCnt_Presenca == 0

    # Valid >= 50 ms pulse (60 ms) on DI0 -> accepted once (+1)
    core.set_raw_channel(0, True, now=2.0)
    core.step(now=2.030)
    assert core.xDI_Presenca is False
    core.step(now=2.060)
    assert core.xDI_Presenca is True
    assert core.uiCnt_Presenca == 1
    core.set_raw_channel(0, False, now=2.100)
    core.step(now=2.100)
    assert core.xDI_Presenca is False
    assert core.uiCnt_Presenca == 1

    # Wrap-around 65535 -> 0
    core.uiCnt_Presenca = 65535
    core.set_raw_channel(0, True, now=3.0)
    core.step(now=3.060)
    assert core.uiCnt_Presenca == 0


def test_ct02_ct03_ct05_remote_auto_and_high_level_b2_interlock() -> None:
    core = PLC500StateCore()
    core.uiHeartbeat_Sup = 1
    core.xCmd_HabRemoto = True
    core.xCmd_Motor = True
    core.step(now=0.1)

    assert core.uiEcho_Heartbeat == 1
    assert core.uiEstado_CLP == ESTADO_NORMAL_AUTO
    assert core.uiCod_Intertrav == INTERTRAV_NENHUM
    assert core.wMascara_DO_Real == 0b1001  # DO0=1, DO3=1

    # Activate B2 (DI1) high level for >= 50 ms -> forces DO1=1 and ALERTA (2)
    core.set_raw_channel(1, True, now=1.0)
    core.step(now=1.060)
    assert core.xDI_NivelAlto is True
    assert core.uiCnt_NivelAlto == 1
    assert core.uiEstado_CLP == ESTADO_ALERTA
    assert core.wMascara_DO_Real == 0b1011  # DO0=1, DO1=1, DO3=1


def test_ct07_ct08_ct09_priority_tree_and_guarded_reset() -> None:
    core = PLC500StateCore()
    core.step(now=0.0)
    core.xCmd_HabRemoto = True
    core.xCmd_Motor = True
    core.uiTimeout_WD_ms = 2000
    core.uiHeartbeat_Sup = 10
    core.step(now=0.1)
    assert core.uiEstado_CLP == ESTADO_NORMAL_AUTO

    # CT-09: Freeze heartbeat for 2.1 s -> Watchdog active (MODO_DEGRADADO=3, code=3)
    core.step(now=2.2)
    assert core.xFlag_WatchdogAtivo is True
    assert core.uiEstado_CLP == ESTADO_MODO_DEGRADADO
    assert core.uiCod_Intertrav == INTERTRAV_WATCHDOG
    assert core.wMascara_DO_Real == 0b0100  # DO0=0, DO1=0, DO2=1, DO3=0

    # CT-08: Overtemperature (DI2) overrides Watchdog -> ALERTA=2, code=2, DO1=1, DO2=1
    core.set_raw_channel(2, True, now=2.5)
    core.step(now=2.56)
    assert core.xDI_Sobretemp is True
    assert core.uiEstado_CLP == ESTADO_ALERTA
    assert core.uiCod_Intertrav == INTERTRAV_SOBRETEMP
    assert core.wMascara_DO_Real == 0b0110  # DO3=0 because watchdog still active

    # CT-07: E-stop NF drop (DI3=0) overrides Overtemp & Watchdog with 0 ms delay
    core.set_raw_channel(3, False, now=2.60)
    core.step(now=2.60)
    assert core.xDI_EmergenciaOK is False
    assert core.xFlag_EmergTravada is True
    assert core.uiCnt_Emergencia == 1
    assert core.uiEstado_CLP == ESTADO_EMERGENCIA_TRAV
    assert core.uiCod_Intertrav == INTERTRAV_EMERGENCIA
    assert core.wMascara_DO_Real == 0b0100

    # Guarded reset blocked while DI3=0
    core.xCmd_ResetFalha = True
    core.step(now=2.65)
    core.xCmd_ResetFalha = False
    core.step(now=2.66)
    assert core.xFlag_EmergTravada is True

    # Restore DI3=1, but DI2=1 still active -> reset still blocked
    core.set_raw_channel(3, True, now=2.70)
    core.xCmd_ResetFalha = True
    core.step(now=2.71)
    core.xCmd_ResetFalha = False
    core.step(now=2.72)
    assert core.xFlag_EmergTravada is True

    # Clear DI2=0 and pulse Coil[3]=1 -> latch clears cleanly
    core.set_raw_channel(2, False, now=2.80)
    core.uiHeartbeat_Sup = 11
    core.step(now=2.81)
    core.xCmd_ResetFalha = True
    core.step(now=2.82)
    core.xCmd_ResetFalha = False
    core.step(now=2.83)
    assert core.xFlag_EmergTravada is False
    assert core.uiEstado_CLP == ESTADO_NORMAL_AUTO


def test_control_api_endpoints_without_tcp_bind() -> None:
    sim = PLC500ModbusSimulator()
    sim._running = True  # bypass TCP bind for fast in-process HTTP endpoint unit tests
    app = create_control_app(sim)

    with TestClient(app) as client:
        r_health = client.get("/health")
        assert r_health.status_code == 200
        assert r_health.json()["status"] == "ok"
        assert r_health.json()["uiVersao_Mapa"] == 100

        r_pulse_short = client.post("/pulse", json={"channel": "di0", "duration_ms": 20})
        assert r_pulse_short.status_code == 200
        assert r_pulse_short.json()["uiCnt_Presenca"] == 0

        r_pulse_long = client.post("/pulse", json={"channel": "di0", "duration_ms": 100})
        assert r_pulse_long.status_code == 200
        assert r_pulse_long.json()["uiCnt_Presenca"] == 1

        r_fault = client.post("/fault", json={"test_case": "CT-07", "di_emergencia_ok": False})
        assert r_fault.status_code == 200
        assert r_fault.json()["xFlag_EmergTravada"] is True
        assert r_fault.json()["uiEstado_CLP"] == 4

        r_reset = client.post("/reset", json={"scenario": "nominal"})
        assert r_reset.status_code == 200
        assert r_reset.json()["xFlag_EmergTravada"] is False
        assert r_reset.json()["uiEstado_CLP"] == 0

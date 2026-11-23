"""Canonical configuration constants and Modbus/ML contracts for WEG PLC500."""

from __future__ import annotations

import os
from enum import IntEnum
from pathlib import Path

# ---------------------------------------------------------------------------
# Sliding-Window, Baseline & Dataset Constants (05_implementacao_nodered.tex)
# ---------------------------------------------------------------------------
W: float = float(os.getenv("ML_WINDOW_SECONDS", "60.0"))
WINDOW_SECONDS: float = W
TP: float = 0.5
POLL_PERIOD_S: float = TP
SAMPLE_RATE_HZ: float = 2.0
K: int = int(round(W / TP))
NOMINAL_SAMPLES_PER_WINDOW: int = K
STRIDE_S: float = 15.0
STRIDE_SAMPLES: int = int(round(STRIDE_S / TP))

BASELINE_MU: float = float(os.getenv("ML_BASELINE_MU", "12.0"))
BASELINE_SIGMA: float = float(os.getenv("ML_BASELINE_SIGMA", "2.5"))
BASELINE_EPS: float = float(os.getenv("ML_ZSCORE_EPS", "0.01"))

SEED: int = 42
RANDOM_SEED: int = SEED

DATASET_HOURS: float = 18.0
TRAIN_RATIO: float = 0.65
VAL_RATIO: float = 0.15
TEST_RATIO: float = 0.20
PURGE_GAP_S: float = 60.0

ZSCORE_ATTENTION_THRESHOLD: float = 2.0
ZSCORE_CRITICAL_THRESHOLD: float = 3.0
DUTY_NIVEL_CRITICAL_THRESHOLD: float = 0.30

# ---------------------------------------------------------------------------
# OT Scan, Debounce, Watchdog & Protocol Constants (04_implementacao_clp.tex)
# ---------------------------------------------------------------------------
SCAN_PERIOD_S: float = 0.01
SCAN_PERIOD_MS: int = 10
DEBOUNCE_S: float = 0.05
DEBOUNCE_MS: int = 50

WD_MIN_MS: int = 2000
WD_MAX_MS: int = 30000
WD_DEFAULT_MS: int = int(os.getenv("PLC_WATCHDOG_TIMEOUT_MS", "5000"))

MAP_VERSION: int = int(os.getenv("PLC_MODBUS_MAP_VERSION", "100"))
UINT16_MOD: int = 65536
UNIT_ID: int = int(os.getenv("PLC_UNIT_ID", os.getenv("UNIT_ID", "1")))

# ---------------------------------------------------------------------------
# 0-Based Modbus Register Maps (03_arquitetura.tex)
# ---------------------------------------------------------------------------
DI_ADDR: dict[str, int] = {
    "xDI_Presenca": 0,
    "xDI_NivelAlto": 1,
    "xDI_Sobretemp": 2,
    "xDI_EmergenciaOK": 3,
    "xFlag_EmergTravada": 4,
    "xFlag_WatchdogAtivo": 5,
}
DI_COUNT: int = 6

COIL_ADDR: dict[str, int] = {
    "xCmd_Motor": 0,
    "xCmd_Valvula": 1,
    "xCmd_Alarme": 2,
    "xCmd_ResetFalha": 3,
    "xCmd_HabRemoto": 4,
}
COIL_COUNT: int = 5

IR_ADDR: dict[str, int] = {
    "uiCnt_Presenca": 0,
    "uiCnt_NivelAlto": 1,
    "uiCnt_Sobretemp": 2,
    "uiCnt_Emergencia": 3,
    "uiEstado_CLP": 4,
    "wMascara_DO_Real": 5,
    "uiEcho_Heartbeat": 6,
    "uiCod_Intertrav": 7,
}
IR_COUNT: int = 8

HR_ADDR: dict[str, int] = {
    "uiHeartbeat_Sup": 0,
    "uiTimeout_WD_ms": 1,
    "uiSeveridade_IA": 2,
    "uiVersao_Mapa": 3,
}
HR_COUNT: int = 4
HR_HEARTBEAT_ALIASES: tuple[str, ...] = (
    "uiHeartbeat_Sup",
    "uiHeartbeat_NR",
    "uiHeartbeat_N8N",
)

DO_BIT: dict[str, int] = {
    "L1_AtuMotor": 0,
    "L2_AtuValvula": 1,
    "L3_SinalAlarme": 2,
    "L4_StatusOper": 3,
}

# ---------------------------------------------------------------------------
# FSM States, Interlock Codes & Severities
# ---------------------------------------------------------------------------
ESTADO_INIT: int = 0
ESTADO_NORMAL_AUTO: int = 1
ESTADO_ALERTA: int = 2
ESTADO_MODO_DEGRADADO: int = 3
ESTADO_EMERGENCIA_TRAV: int = 4


class PlcState(IntEnum):
    INIT = 0
    NORMAL_AUTO = 1
    ALERTA = 2
    MODO_DEGRADADO = 3
    EMERGENCIA_TRAV = 4


INTERTRAV_NENHUM: int = 0
INTERTRAV_EMERGENCIA: int = 1
INTERTRAV_SOBRETEMP: int = 2
INTERTRAV_WATCHDOG: int = 3


class InterlockCode(IntEnum):
    NENHUM = 0
    EMERGENCIA = 1
    SOBRETEMP = 2
    WATCHDOG = 3


SEVERITY_NORMAL: int = 0
SEVERITY_ATENCAO: int = 1
SEVERITY_CRITICO: int = 2
SEVERITY_EMERGENCIA: int = 3


class SeverityLevel(IntEnum):
    NORMAL = 0
    ATENCAO = 1
    CRITICO = 2
    EMERGENCIA = 3


SEVERITY_LABELS: tuple[str, ...] = ("NORMAL", "ATENCAO", "CRITICO", "EMERGENCIA")
SEVERITY_CODE_TO_NAME: dict[int, str] = {
    0: "NORMAL",
    1: "ATENCAO",
    2: "CRITICO",
    3: "EMERGENCIA",
}
SEVERITY_NAME_TO_CODE: dict[str, int] = {name: code for code, name in SEVERITY_CODE_TO_NAME.items()}

# ---------------------------------------------------------------------------
# Network & Service Ports
# ---------------------------------------------------------------------------
PLC_HOST: str = os.getenv("PLC_HOST", "192.168.10.10")
PLC_PORT: int = int(os.getenv("PLC_PORT", "502"))

SIM_HOST: str = os.getenv("PLC_SIM_HOST", "0.0.0.0")
SIM_MODBUS_PORT: int = int(os.getenv("PLC_SIM_PORT", "502"))
SIM_API_PORT: int = int(
    os.getenv("PLC_SIM_CONTROL_PORT") or os.getenv("PLC_SIM_API_PORT") or "8502"
)

ML_API_HOST: str = os.getenv("ML_API_HOST", "0.0.0.0")
ML_API_PORT: int = int(os.getenv("ML_API_PORT", "8000"))

# ---------------------------------------------------------------------------
# Paths & Model/Data Artifact Resolution
# ---------------------------------------------------------------------------
PACKAGE_DIR: Path = Path(__file__).resolve().parent
PYTHON_ROOT: Path = PACKAGE_DIR.parent
REPO_ROOT: Path = PYTHON_ROOT.parent


def resolve_models_dir(models_dir: Path | str | None = None) -> Path:
    """Resolve the models directory cleanly across local repo and Docker layouts."""
    if models_dir is not None:
        return Path(models_dir)

    env_val = os.getenv("ML_MODELS_DIR")
    if env_val:
        candidate = Path(env_val)
        if candidate.is_absolute() or candidate.exists():
            return candidate
        repo_candidate = REPO_ROOT / candidate
        if repo_candidate.exists():
            return repo_candidate
        return PYTHON_ROOT / "models"

    return PYTHON_ROOT / "models"


MODELS_DIR: Path = resolve_models_dir()
ANOMALY_MODEL_PATH: Path = MODELS_DIR / "anomaly_detector.joblib"
SEVERITY_MODEL_PATH: Path = MODELS_DIR / "severity_classifier.joblib"
MODEL_METADATA_PATH: Path = MODELS_DIR / "model_metadata.json"
EVALUATION_REPORT_PATH: Path = MODELS_DIR / "evaluation_report.md"

DATA_DIR: Path = REPO_ROOT / "data"
RAW_DATA_PATH: Path = DATA_DIR / "raw" / "raw_telemetry_2hz.csv.gz"
RAW_SAMPLE_PATH: Path = DATA_DIR / "raw" / "raw_sample_500.csv"
PROCESSED_FEATURES_PATH: Path = DATA_DIR / "processed" / "window_features_60s.csv"
SCENARIO_SUMMARY_PATH: Path = DATA_DIR / "processed" / "scenario_summary.json"

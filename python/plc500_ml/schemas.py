"""JSON Schema and strict Pydantic v2 contracts for WEG PLC500 ML API."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Any, Literal

import jsonschema
from pydantic import AliasChoices, BaseModel, ConfigDict, Field, StrictBool, StrictStr

from plc500_ml.config import (
    BASELINE_EPS,
    BASELINE_MU,
    BASELINE_SIGMA,
    MAP_VERSION,
    WINDOW_SECONDS,
)

# Exact JSON Schema from docs/relatorio/capitulos/05_implementacao_nodered.tex:220-249
DIAGNOSTICO_ATUACAO_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "DiagnosticoAtuacaoPLC500",
    "type": "object",
    "additionalProperties": False,
    "required": [
        "severidade",
        "diagnostico",
        "causa_provavel",
        "comandos_atuadores",
        "requer_relatorio",
        "resumo_executivo",
    ],
    "properties": {
        "severidade": {
            "type": "string",
            "enum": ["NORMAL", "ATENCAO", "CRITICO", "EMERGENCIA"],
        },
        "diagnostico": {
            "type": "string",
            "minLength": 10,
            "maxLength": 400,
        },
        "causa_provavel": {
            "type": "string",
            "minLength": 5,
            "maxLength": 250,
        },
        "comandos_atuadores": {
            "type": "object",
            "additionalProperties": False,
            "required": ["cmd_motor", "cmd_valvula", "cmd_alarme"],
            "properties": {
                "cmd_motor": {"type": "boolean"},
                "cmd_valvula": {"type": "boolean"},
                "cmd_alarme": {"type": "boolean"},
            },
        },
        "requer_relatorio": {"type": "boolean"},
        "resumo_executivo": {
            "type": "string",
            "minLength": 20,
            "maxLength": 800,
        },
    },
}

_DIAGNOSTICO_VALIDATOR = jsonschema.Draft202012Validator(DIAGNOSTICO_ATUACAO_SCHEMA)

SeverityLabel = Literal["NORMAL", "ATENCAO", "CRITICO", "EMERGENCIA"]


def get_diagnostico_json_schema() -> dict[str, Any]:
    """Return a deep copy of the canonical DiagnosticoAtuacaoPLC500 JSON Schema."""
    return deepcopy(DIAGNOSTICO_ATUACAO_SCHEMA)


def validate_diagnostico_json_schema(payload: dict[str, Any]) -> None:
    """Validate a diagnosis dictionary strictly against Draft 2020-12 JSON Schema."""
    _DIAGNOSTICO_VALIDATOR.validate(payload)


class ComandosAtuadores(BaseModel):
    """Strict actuator command object matching DiagnosticoAtuacaoPLC500."""

    model_config = ConfigDict(extra="forbid")

    cmd_motor: StrictBool
    cmd_valvula: StrictBool
    cmd_alarme: StrictBool


class DiagnosticoAtuacaoPLC500(BaseModel):
    """Strict Pydantic v2 representation of DiagnosticoAtuacaoPLC500."""

    model_config = ConfigDict(extra="forbid")

    severidade: SeverityLabel
    diagnostico: StrictStr = Field(min_length=10, max_length=400)
    causa_provavel: StrictStr = Field(min_length=5, max_length=250)
    comandos_atuadores: ComandosAtuadores
    requer_relatorio: StrictBool
    resumo_executivo: StrictStr = Field(min_length=20, max_length=800)


class TelemetrySample(BaseModel):
    """Single 2 Hz telemetry sample matching telemetria_sensores and PLC500 Modbus registers."""

    model_config = ConfigDict(extra="ignore")

    capturado_em: datetime | str | None = None
    di_presenca: bool
    di_nivel_alto: bool
    di_sobretemp: bool
    di_emergencia_ok: bool
    flag_emerg_travada: bool = False
    flag_watchdog: bool = False
    cnt_presenca: int = Field(ge=0, le=65535)
    cnt_nivel_alto: int = Field(default=0, ge=0, le=65535)
    cnt_sobretemp: int = Field(default=0, ge=0, le=65535)
    cnt_emergencia: int = Field(default=0, ge=0, le=65535)
    estado_clp: int = Field(default=1, ge=0, le=4)
    mascara_do_real: int = Field(default=0, ge=0, le=15)
    cod_intertrav: int = Field(default=0, ge=0, le=3)


class RawWindowRequest(BaseModel):
    """Request body for POST /predict/raw-window."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    samples: list[TelemetrySample] = Field(min_length=1)
    window_seconds: float = Field(
        default=WINDOW_SECONDS,
        gt=0.0,
        validation_alias=AliasChoices("window_seconds", "window_s"),
    )
    baseline_mu: float = Field(default=BASELINE_MU, ge=0.0)
    baseline_sigma: float = Field(default=BASELINE_SIGMA, ge=0.0)
    zscore_eps: float = Field(default=BASELINE_EPS, gt=0.0)
    ciclo_sumario_horario: bool = False


class PredictRequest(BaseModel):
    """Request body for POST /predict — compatible directly with Node-RED msg.payload."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    taxa_presenca_min: float = Field(
        ge=0.0,
        validation_alias=AliasChoices("taxa_presenca_min", "lambda1"),
    )
    duty_nivel: float = Field(ge=0.0, le=1.0)
    duty_sobretemp: float = Field(ge=0.0, le=1.0)
    zscore_presenca: float | None = Field(
        default=None,
        validation_alias=AliasChoices("zscore_presenca", "z_score"),
    )
    chattering_index: float | None = Field(default=None, ge=0.0, le=1.0)
    amostras_k: int = Field(default=120, ge=1)
    window_seconds: float = Field(
        default=WINDOW_SECONDS,
        gt=0.0,
        validation_alias=AliasChoices("window_seconds", "window_s"),
    )
    di_emergencia_ok: bool = True
    flag_emerg_travada: bool = False
    flag_watchdog: bool = False
    di_sobretemp: bool = False
    di_nivel_alto: bool = False
    estado_clp: int = Field(default=1, ge=0, le=4)
    cod_intertrav: int = Field(default=0, ge=0, le=3)
    baseline_mu: float = Field(
        default=BASELINE_MU,
        ge=0.0,
        validation_alias=AliasChoices("baseline_mu", "baseline_mu_presenca"),
    )
    baseline_sigma: float = Field(
        default=BASELINE_SIGMA,
        ge=0.0,
        validation_alias=AliasChoices("baseline_sigma", "baseline_sigma_presenca"),
    )
    zscore_eps: float = Field(default=BASELINE_EPS, gt=0.0)
    ciclo_sumario_horario: bool = False


class PredictionResponse(BaseModel):
    """Unified ML + Safety Guardian response returned by POST /predict and /predict/raw-window."""

    diagnostico_atuacao: DiagnosticoAtuacaoPLC500
    severidade_codigo: int = Field(ge=0, le=3)
    severidade_estatistica: int = Field(ge=0, le=3)
    severidade_ml: int | None = Field(default=None, ge=0, le=3)
    anomaly_score: float | None = None
    is_anomaly: bool = False
    ml_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    origem_decisao: Literal["ML_FASTAPI", "FALLBACK_STATS"]
    monotonicity_applied: bool = False
    veto_reasons: list[str] = Field(default_factory=list)
    features: dict[str, float | int | bool]
    model_version: str


class HealthResponse(BaseModel):
    """Health check response for GET /health."""

    status: Literal["ok", "degraded"]
    service: str = "plc500-ml-api"
    models_loaded: bool
    fallback_mode: bool
    schema_title: str = "DiagnosticoAtuacaoPLC500"
    map_version: int = MAP_VERSION
    model_version: str | None = None


class ModelInfoResponse(BaseModel):
    """Metadata and model diagnostics response for GET /model-info."""

    models_loaded: bool
    load_error: str | None = None
    model_version: str | None = None
    anomaly_model: dict[str, Any] = Field(default_factory=dict)
    severity_model: dict[str, Any] = Field(default_factory=dict)
    feature_names: list[str]
    baseline: dict[str, float]
    training_metadata: dict[str, Any] = Field(default_factory=dict)

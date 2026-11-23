"""FastAPI inference service for WEG PLC500 hybrid anomaly & safety diagnosis."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException, Request

from plc500_ml.config import ML_API_HOST, ML_API_PORT
from plc500_ml.features import (
    EmptyWindowError,
    extract_features_from_raw_window,
    extract_features_from_summary,
)
from plc500_ml.inference import PLC500InferenceEngine
from plc500_ml.schemas import (
    HealthResponse,
    ModelInfoResponse,
    PredictionResponse,
    PredictRequest,
    RawWindowRequest,
    get_diagnostico_json_schema,
)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Initialize inference engine at FastAPI startup."""
    if not hasattr(app.state, "engine") or app.state.engine is None:
        app.state.engine = PLC500InferenceEngine()
    yield


app = FastAPI(
    title="WEG PLC500 ML Inference API",
    version="1.0.0",
    description=(
        "Hybrid statistical + ML anomaly detection and severity diagnosis service "
        "with Monotonicity Safety Guardian and JSON Schema Draft 2020-12 enforcement."
    ),
    lifespan=lifespan,
)


def get_engine(request: Request) -> PLC500InferenceEngine:
    """Retrieve or lazily create the app-scoped PLC500InferenceEngine."""
    engine = getattr(request.app.state, "engine", None)
    if engine is None:
        engine = PLC500InferenceEngine()
        request.app.state.engine = engine
    return engine


@app.get("/health", response_model=HealthResponse)
def health(request: Request) -> HealthResponse:
    return get_engine(request).health()


@app.get("/schema")
def schema() -> dict[str, Any]:
    return get_diagnostico_json_schema()


@app.get("/model-info", response_model=ModelInfoResponse)
def model_info(request: Request) -> ModelInfoResponse:
    return get_engine(request).model_info()


@app.post("/predict", response_model=PredictionResponse)
def predict(payload: PredictRequest, request: Request) -> PredictionResponse:
    try:
        wf = extract_features_from_summary(payload)
    except EmptyWindowError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return get_engine(request).predict_from_features(
        wf,
        ciclo_sumario_horario=payload.ciclo_sumario_horario,
    )


@app.post("/predict/raw-window", response_model=PredictionResponse)
def predict_raw_window(payload: RawWindowRequest, request: Request) -> PredictionResponse:
    try:
        wf = extract_features_from_raw_window(
            payload.samples,
            window_s=payload.window_seconds,
            mu=payload.baseline_mu,
            sigma=payload.baseline_sigma,
            eps=payload.zscore_eps,
        )
    except EmptyWindowError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return get_engine(request).predict_from_features(
        wf,
        ciclo_sumario_horario=payload.ciclo_sumario_horario,
    )


def main() -> None:
    uvicorn.run("plc500_ml.api:app", host=ML_API_HOST, port=ML_API_PORT)


if __name__ == "__main__":
    main()

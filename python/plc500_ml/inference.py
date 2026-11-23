"""ML + Safety Guardian inference engine with deterministic fallback for WEG PLC500."""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import joblib
import jsonschema
import numpy as np

from plc500_ml.config import (
    BASELINE_EPS,
    BASELINE_MU,
    BASELINE_SIGMA,
    MAP_VERSION,
    SEVERITY_CODE_TO_NAME,
    resolve_models_dir,
)
from plc500_ml.features import (
    MODEL_FEATURE_COLUMNS,
    WindowFeatures,
    extract_features_from_raw_window,
    extract_features_from_summary,
)
from plc500_ml.safety_rules import (
    apply_monotonicity_guard,
    build_fallback_diagnosis,
    evaluate_statistical_fallback,
)
from plc500_ml.schemas import (
    DiagnosticoAtuacaoPLC500,
    HealthResponse,
    ModelInfoResponse,
    PredictionResponse,
    validate_diagnostico_json_schema,
)

logger = logging.getLogger(__name__)


def _clamp_text(text: str, min_len: int, max_len: int) -> str:
    cleaned = " ".join(str(text).split()).strip()
    if len(cleaned) < min_len:
        cleaned = (cleaned + " (verificado pelo motor ML).").strip()
        while len(cleaned) < min_len:
            cleaned += "."
    if len(cleaned) > max_len:
        cleaned = cleaned[: max_len - 3].rstrip() + "..."
    return cleaned


class PLC500InferenceEngine:
    """Combines IsolationForest + SeverityClassifier + Monotonicity Guard + JSON Schema."""

    def __init__(self, models_dir: Path | str | None = None) -> None:
        self.models_dir = resolve_models_dir(models_dir)
        self.anomaly_detector: Any = None
        self.severity_classifier: Any = None
        self.metadata: dict[str, Any] = {}
        self.feature_columns: list[str] = list(MODEL_FEATURE_COLUMNS)
        self.model_version: str = "fallback-only"
        self.models_loaded: bool = False
        self.load_error: str | None = None
        self._load_artifacts()

    def _load_artifacts(self) -> None:
        anom_path = self.models_dir / "anomaly_detector.joblib"
        clf_path = self.models_dir / "severity_classifier.joblib"
        meta_path = self.models_dir / "model_metadata.json"

        try:
            if not anom_path.is_file() or not clf_path.is_file():
                raise FileNotFoundError(f"Required model artifacts not found in {self.models_dir}")

            self.anomaly_detector = joblib.load(anom_path)
            self.severity_classifier = joblib.load(clf_path)

            if meta_path.is_file():
                self.metadata = json.loads(meta_path.read_text(encoding="utf-8"))
                self.model_version = str(self.metadata.get("model_version", "1.0.0"))
                meta_feats = self.metadata.get("feature_names")
                if isinstance(meta_feats, list) and meta_feats:
                    self.feature_columns = [str(c) for c in meta_feats]
            else:
                self.metadata = {}
                self.model_version = "1.0.0"

            self.models_loaded = True
            self.load_error = None
        except Exception as exc:  # noqa: BLE001
            logger.warning("Running in deterministic fallback mode: %s", exc)
            self.anomaly_detector = None
            self.severity_classifier = None
            self.metadata = {}
            self.feature_columns = list(MODEL_FEATURE_COLUMNS)
            self.model_version = "fallback-only"
            self.models_loaded = False
            self.load_error = str(exc)

    def _build_ml_candidate(
        self,
        wf: WindowFeatures,
        ml_severity: int,
        is_anomaly: bool,
        anomaly_score: float,
        ml_confidence: float,
    ) -> dict[str, Any]:
        sev_label = SEVERITY_CODE_TO_NAME[ml_severity]

        if ml_severity == 3:
            cmd_motor, cmd_valvula, cmd_alarme = False, False, True
            diag = (
                f"Classificador ML identificou EMERGENCIA (conf={ml_confidence:.2f}, "
                f"anom={is_anomaly}); atuando desligamento seguro."
            )
            causa = "Condicao de parada de emergencia ou intertravamento critico em DI3."
        elif ml_severity == 2:
            cmd_motor, cmd_valvula, cmd_alarme = False, True, True
            if wf.veto_sobretemp:
                causa = "Sobretemperatura detectada em B3 (DI2) com risco termico ao motor."
            elif wf.veto_watchdog:
                causa = "Timeout de comunicacao Modbus TCP (watchdog ativo)."
            elif wf.veto_nivel_sustentado:
                causa = "Nivel alto sustentado em B2 (DI1) com risco de transbordamento."
            elif wf.zscore_presenca >= 3.0 or wf.chattering_index >= 0.7:
                causa = "Oscilacao rapida ou repique (chattering) no sensor B1."
            else:
                causa = "Anomalia critica de cadencia ou bloqueio na esteira B1."
            diag = (
                f"Classificador ML identificou severidade CRITICO (conf={ml_confidence:.2f}, "
                f"z={wf.zscore_presenca:.2f}, score={anomaly_score:.3f})."
            )
        elif ml_severity == 1:
            cmd_motor, cmd_valvula, cmd_alarme = True, bool(wf.duty_nivel > 0.0), False
            causa = (
                "Nivel alto intermitente em B2."
                if wf.duty_nivel > 0.0
                else "Desvio moderado de cadencia em B1."
            )
            diag = (
                f"Classificador ML identificou ATENCAO (conf={ml_confidence:.2f}, "
                f"taxa={wf.taxa_presenca_min:.1f} ev/min, z={wf.zscore_presenca:.2f})."
            )
        else:
            cmd_motor, cmd_valvula, cmd_alarme = True, False, False
            causa = "Operacao nominal estavel sem falhas ou desvios significativos."
            diag = (
                f"Operacao NORMAL confirmada pelo modelo ML (conf={ml_confidence:.2f}, "
                f"score={anomaly_score:.3f}, taxa={wf.taxa_presenca_min:.1f} ev/min)."
            )

        resumo = (
            f"Inferencia ML_FASTAPI (v{self.model_version}): severidade={sev_label}, "
            f"confianca={ml_confidence:.2f}, anomaly_score={anomaly_score:.3f}, "
            f"is_anomaly={is_anomaly}, taxa={wf.taxa_presenca_min:.1f} ev/min, "
            f"z={wf.zscore_presenca:.2f}, duty_nivel={wf.duty_nivel:.2f}, "
            f"duty_sobretemp={wf.duty_sobretemp:.2f}, chattering={wf.chattering_index:.2f}."
        )

        return {
            "severidade": sev_label,
            "diagnostico": _clamp_text(diag, 10, 400),
            "causa_provavel": _clamp_text(causa, 5, 250),
            "comandos_atuadores": {
                "cmd_motor": cmd_motor,
                "cmd_valvula": cmd_valvula,
                "cmd_alarme": cmd_alarme,
            },
            "requer_relatorio": bool(ml_severity >= 1),
            "resumo_executivo": _clamp_text(resumo, 20, 800),
        }

    def predict_from_features(
        self,
        wf: WindowFeatures,
        *,
        ciclo_sumario_horario: bool = False,
    ) -> PredictionResponse:
        """Predict severity and guarded actuator commands from WindowFeatures."""
        stat_fb = evaluate_statistical_fallback(wf, ciclo_sumario_horario=ciclo_sumario_horario)

        if not self.models_loaded:
            fb_payload = build_fallback_diagnosis(wf, stat_fb)
            guarded = apply_monotonicity_guard(fb_payload, wf, stat_fb)
            validate_diagnostico_json_schema(guarded.diagnostico_payload)
            return PredictionResponse(
                diagnostico_atuacao=DiagnosticoAtuacaoPLC500(**guarded.diagnostico_payload),
                severidade_codigo=guarded.final_severity_code,
                severidade_estatistica=stat_fb.severity_code,
                severidade_ml=None,
                anomaly_score=None,
                is_anomaly=(stat_fb.severity_code >= 1),
                ml_confidence=None,
                origem_decisao="FALLBACK_STATS",
                monotonicity_applied=guarded.guard_applied,
                veto_reasons=guarded.guard_reasons,
                features=wf.to_dict(),
                model_version=self.model_version,
            )

        model_dict = wf.to_model_dict()
        vector = [float(model_dict[col]) for col in self.feature_columns]
        X = np.array([vector], dtype=float)

        anomaly_score = float(self.anomaly_detector.decision_function(X)[0])
        is_anomaly = bool(int(self.anomaly_detector.predict(X)[0]) == -1)

        ml_severity = max(0, min(3, int(self.severity_classifier.predict(X)[0])))
        if hasattr(self.severity_classifier, "predict_proba"):
            proba = self.severity_classifier.predict_proba(X)[0]
            ml_confidence = float(np.max(proba))
        else:
            ml_confidence = 1.0

        candidate = self._build_ml_candidate(
            wf=wf,
            ml_severity=ml_severity,
            is_anomaly=is_anomaly,
            anomaly_score=anomaly_score,
            ml_confidence=ml_confidence,
        )

        try:
            guarded = apply_monotonicity_guard(candidate, wf, stat_fb)
            validate_diagnostico_json_schema(guarded.diagnostico_payload)
            origem = "ML_FASTAPI"
        except jsonschema.ValidationError as exc:
            logger.warning("Schema validation failed on ML candidate, using fallback: %s", exc)
            fb_payload = build_fallback_diagnosis(wf, stat_fb)
            guarded = apply_monotonicity_guard(fb_payload, wf, stat_fb)
            validate_diagnostico_json_schema(guarded.diagnostico_payload)
            origem = "FALLBACK_STATS"

        return PredictionResponse(
            diagnostico_atuacao=DiagnosticoAtuacaoPLC500(**guarded.diagnostico_payload),
            severidade_codigo=guarded.final_severity_code,
            severidade_estatistica=stat_fb.severity_code,
            severidade_ml=ml_severity,
            anomaly_score=round(anomaly_score, 6),
            is_anomaly=is_anomaly,
            ml_confidence=round(ml_confidence, 6),
            origem_decisao=origem,
            monotonicity_applied=guarded.guard_applied,
            veto_reasons=guarded.guard_reasons,
            features=wf.to_dict(),
            model_version=self.model_version,
        )

    def predict(
        self,
        features_or_samples: WindowFeatures | Mapping[str, Any] | Sequence[Mapping[str, Any]],
        *,
        ciclo_sumario_horario: bool = False,
    ) -> PredictionResponse:
        """Predict from WindowFeatures, a feature dictionary, or a sequence of raw samples."""
        if isinstance(features_or_samples, WindowFeatures):
            wf = features_or_samples
        elif isinstance(features_or_samples, Mapping):
            wf = extract_features_from_summary(features_or_samples)
        elif isinstance(features_or_samples, Sequence):
            wf = extract_features_from_raw_window(features_or_samples)
        else:
            raise TypeError(f"Unsupported input type: {type(features_or_samples).__name__}")
        return self.predict_from_features(wf, ciclo_sumario_horario=ciclo_sumario_horario)

    def health(self) -> HealthResponse:
        return HealthResponse(
            status="ok" if self.models_loaded else "degraded",
            models_loaded=self.models_loaded,
            fallback_mode=not self.models_loaded,
            map_version=MAP_VERSION,
            model_version=self.model_version if self.models_loaded else None,
        )

    def model_info(self) -> ModelInfoResponse:
        anom_meta = self.metadata.get("anomaly_detector", {})
        clf_meta = self.metadata.get("severity_classifier", {})
        return ModelInfoResponse(
            models_loaded=self.models_loaded,
            load_error=self.load_error,
            model_version=self.model_version if self.models_loaded else None,
            anomaly_model=anom_meta if isinstance(anom_meta, dict) else {},
            severity_model=clf_meta if isinstance(clf_meta, dict) else {},
            feature_names=list(self.feature_columns),
            baseline={
                "mu": BASELINE_MU,
                "sigma": BASELINE_SIGMA,
                "eps": BASELINE_EPS,
            },
            training_metadata={
                k: v
                for k, v in self.metadata.items()
                if k not in {"anomaly_detector", "severity_classifier"}
            },
        )


PLC500Predictor = PLC500InferenceEngine
_DEFAULT_ENGINE: PLC500InferenceEngine | None = None


def _get_default_engine() -> PLC500InferenceEngine:
    global _DEFAULT_ENGINE
    if _DEFAULT_ENGINE is None:
        _DEFAULT_ENGINE = PLC500InferenceEngine()
    return _DEFAULT_ENGINE


def predict_window(
    features_or_samples: WindowFeatures | Mapping[str, Any] | Sequence[Mapping[str, Any]],
    *,
    ciclo_sumario_horario: bool = False,
) -> PredictionResponse:
    """Run inference on a window feature dict, WindowFeatures, or raw sample sequence."""
    return _get_default_engine().predict(
        features_or_samples,
        ciclo_sumario_horario=ciclo_sumario_horario,
    )


predict_features = predict_window
predict = predict_window
run_inference = predict_window

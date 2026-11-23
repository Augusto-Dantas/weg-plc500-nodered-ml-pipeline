"""Deterministic statistical fallback and Monotonicity Safety Guardian."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from plc500_ml.config import (
    DUTY_NIVEL_CRITICAL_THRESHOLD,
    SEVERITY_CODE_TO_NAME,
    SEVERITY_NAME_TO_CODE,
    ZSCORE_ATTENTION_THRESHOLD,
    ZSCORE_CRITICAL_THRESHOLD,
)
from plc500_ml.features import WindowFeatures, extract_features_from_summary
from plc500_ml.schemas import validate_diagnostico_json_schema

SEVERITY_CODE_TO_LABEL: dict[int, str] = dict(SEVERITY_CODE_TO_NAME)
SEVERITY_LABEL_TO_CODE: dict[str, int] = dict(SEVERITY_NAME_TO_CODE)


def _coerce_window_features(wf: WindowFeatures | Mapping[str, Any]) -> WindowFeatures:
    """Coerce a WindowFeatures instance or summary feature mapping into WindowFeatures."""
    if isinstance(wf, WindowFeatures):
        return wf
    if isinstance(wf, Mapping):
        return extract_features_from_summary(wf)
    raise TypeError(f"Expected WindowFeatures or Mapping, got {type(wf).__name__}")


@dataclass(frozen=True, slots=True)
class StatisticalFallbackResult:
    """Deterministic statistical severity & actuator output matching Quadro codigo_js."""

    severity_code: int
    severity_label: str
    cmd_motor: bool
    cmd_valvula: bool
    cmd_alarme: bool
    requer_relatorio: bool
    acionar_llm: bool
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class GuardedDecision:
    """Post-validation decision guarded by safety monotonicity and local vetoes."""

    diagnostico_payload: dict[str, Any]
    guard_applied: bool
    guard_reasons: list[str]
    raw_severity_code: int
    statistical_severity_code: int
    final_severity_code: int

    def model_dump(self) -> dict[str, Any]:
        """Return a copy of the guarded diagnosis payload dictionary."""
        return dict(self.diagnostico_payload)

    def keys(self) -> Any:
        return self.diagnostico_payload.keys()

    def __iter__(self) -> Any:
        return iter(self.diagnostico_payload)

    def __getitem__(self, key: str) -> Any:
        return self.diagnostico_payload[key]


def _clamp_str(text: str, min_len: int, max_len: int, pad_suffix: str = " (verificado).") -> str:
    cleaned = " ".join(str(text).split()).strip()
    if len(cleaned) < min_len:
        cleaned = (cleaned + pad_suffix).strip()
        while len(cleaned) < min_len:
            cleaned += "."
    if len(cleaned) > max_len:
        cleaned = cleaned[: max_len - 3].rstrip() + "..."
    return cleaned


def evaluate_statistical_fallback(
    wf: WindowFeatures | Mapping[str, Any],
    *,
    ciclo_sumario_horario: bool = False,
) -> StatisticalFallbackResult:
    """Execute deterministic statistical severity matrix (05_implementacao_nodered.tex:170-182)."""
    wf = _coerce_window_features(wf)
    reasons: list[str] = []

    if wf.veto_emergencia:
        severity_code = 3
        cmd_motor = False
        cmd_valvula = False
        cmd_alarme = True
        if wf.flag_emerg_travada:
            reasons.append("flag_emerg_travada")
        if not wf.di_emergencia_ok:
            reasons.append("di_emergencia_ok=False")
    elif (
        wf.veto_sobretemp
        or wf.veto_nivel_sustentado
        or abs(wf.zscore_presenca) >= ZSCORE_CRITICAL_THRESHOLD
        or wf.veto_watchdog
    ):
        severity_code = 2
        cmd_motor = False
        cmd_valvula = True
        cmd_alarme = True
        if wf.veto_sobretemp:
            reasons.append("sobretemperatura")
        if wf.veto_nivel_sustentado:
            reasons.append("nivel_alto_sustentado")
        if abs(wf.zscore_presenca) >= ZSCORE_CRITICAL_THRESHOLD:
            reasons.append("zscore_critico")
        if wf.veto_watchdog:
            reasons.append("watchdog_ativo")
    elif wf.duty_nivel > 0.0 or abs(wf.zscore_presenca) >= ZSCORE_ATTENTION_THRESHOLD:
        severity_code = 1
        cmd_motor = True
        cmd_valvula = True
        cmd_alarme = False
        if wf.duty_nivel > 0.0:
            reasons.append("nivel_alto_intermitente")
        if abs(wf.zscore_presenca) >= ZSCORE_ATTENTION_THRESHOLD:
            reasons.append("desvio_cadencia_atencao")
    else:
        severity_code = 0
        cmd_motor = True
        cmd_valvula = False
        cmd_alarme = False
        reasons.append("operacao_nominal")

    return StatisticalFallbackResult(
        severity_code=severity_code,
        severity_label=SEVERITY_CODE_TO_LABEL[severity_code],
        cmd_motor=cmd_motor,
        cmd_valvula=cmd_valvula,
        cmd_alarme=cmd_alarme,
        requer_relatorio=(severity_code >= 1),
        acionar_llm=(severity_code >= 1) or bool(ciclo_sumario_horario),
        reasons=tuple(reasons),
    )


def compute_statistical_severity(wf: WindowFeatures | Mapping[str, Any]) -> int:
    """Return deterministic statistical severity integer in {0, 1, 2, 3}."""
    return evaluate_statistical_fallback(wf).severity_code


def build_fallback_diagnosis(
    wf: WindowFeatures | Mapping[str, Any],
    fb: StatisticalFallbackResult | None = None,
) -> dict[str, Any]:
    """Construct a schema-valid Portuguese technical diagnosis from statistical rules."""
    wf = _coerce_window_features(wf)
    stat = fb or evaluate_statistical_fallback(wf)

    if stat.severity_code == 3:
        diagnostico = (
            "Parada de emergencia ativa no PLC500 (DI3 NF aberto ou trava de emergencia ativa)."
        )
        causa = "Acionamento de botoeira de emergencia B4 ou ruptura de cabo em DI3."
    elif stat.severity_code == 2:
        if wf.veto_sobretemp:
            diagnostico = (
                f"Sobretemperatura detectada em DI2 (duty={wf.duty_sobretemp:.2f}); "
                "motor vetado e valvula de alivio acionada."
            )
            causa = "Sobrecarga termica no conjunto motor-bomba detectada por B3."
        elif wf.veto_watchdog:
            diagnostico = "Timeout de watchdog Modbus TCP detectado; CLP em modo degradado seguro."
            causa = "Perda de enlace ou estagnacao do heartbeat em HR[0] acima do limite."
        elif wf.veto_nivel_sustentado:
            diagnostico = (
                f"Nivel alto sustentado em B2 (duty={wf.duty_nivel:.2f} >= "
                f"{DUTY_NIVEL_CRITICAL_THRESHOLD:.2f}); risco de transbordamento."
            )
            causa = "Acumulo continuo no reservatorio sem escoamento suficiente."
        elif wf.zscore_presenca >= ZSCORE_CRITICAL_THRESHOLD:
            diagnostico = (
                f"Chattering critico em B1 (lambda={wf.taxa_presenca_min:.1f} ev/min, "
                f"z={wf.zscore_presenca:.2f})."
            )
            causa = "Oscilacao rapida ou repique eletrico no sensor de presenca DI0."
        else:
            diagnostico = (
                f"Bloqueio ou estol da esteira em B1 (lambda={wf.taxa_presenca_min:.1f} ev/min, "
                f"z={wf.zscore_presenca:.2f})."
            )
            causa = "Interrupcao de fluxo de pecas ou travamento mecanico na esteira."
    elif stat.severity_code == 1:
        if wf.duty_nivel > 0.0:
            diagnostico = (
                f"Nivel alto intermitente detectado em B2 (duty={wf.duty_nivel:.2f}) "
                "com abertura preventiva de valvula."
            )
            causa = "Oscilacao transitoria do nivel do reservatorio acima de B2."
        else:
            diagnostico = (
                f"Desvio moderado de cadencia em B1 (lambda={wf.taxa_presenca_min:.1f} ev/min, "
                f"z={wf.zscore_presenca:.2f})."
            )
            causa = "Variacao na taxa de chegada de pecas fora da faixa nominal."
    else:
        diagnostico = (
            f"Operacao nominal estavel (lambda={wf.taxa_presenca_min:.1f} ev/min, "
            f"z={wf.zscore_presenca:.2f})."
        )
        causa = "Parametros discretos e cadencia dentro da linha de base nominal."

    resumo = (
        f"Janela de {wf.window_seconds:.0f}s ({wf.amostras_k} amostras): "
        f"severidade {stat.severity_label}, taxa={wf.taxa_presenca_min:.1f} ev/min, "
        f"z={wf.zscore_presenca:.2f}, duty_nivel={wf.duty_nivel:.2f}, "
        f"duty_sobretemp={wf.duty_sobretemp:.2f}, chattering={wf.chattering_index:.2f}. "
        f"Comandos: motor={stat.cmd_motor}, valvula={stat.cmd_valvula}, alarme={stat.cmd_alarme}."
    )

    payload: dict[str, Any] = {
        "severidade": stat.severity_label,
        "diagnostico": _clamp_str(diagnostico, 10, 400),
        "causa_provavel": _clamp_str(causa, 5, 250),
        "comandos_atuadores": {
            "cmd_motor": bool(stat.cmd_motor),
            "cmd_valvula": bool(stat.cmd_valvula),
            "cmd_alarme": bool(stat.cmd_alarme),
        },
        "requer_relatorio": bool(stat.requer_relatorio),
        "resumo_executivo": _clamp_str(resumo, 20, 800),
    }
    validate_diagnostico_json_schema(payload)
    return payload


def deterministic_fallback(wf: WindowFeatures | Mapping[str, Any]) -> dict[str, Any]:
    """Single-argument deterministic statistical fallback diagnosis entry point."""
    return build_fallback_diagnosis(wf)


fallback_diagnosis = deterministic_fallback


def apply_monotonicity_guard(
    candidate: Mapping[str, Any],
    wf: WindowFeatures | Mapping[str, Any],
    stat_fb: StatisticalFallbackResult | None = None,
) -> GuardedDecision:
    """Enforce Monotonicity Safety Guardian over ML/LLM candidate decisions.

    Guarantees:
    1. final_severity >= statistical_severity (never downgrade CRITICO/EMERGENCIA/ATENCAO).
    2. Emergency state forces {cmd_motor: False, cmd_valvula: False, cmd_alarme: True}.
    3. Any active local safety veto (sobretemp, sustained high level, E-stop, watchdog) or
       severity >= CRITICO strictly blocks cmd_motor = True.
    4. Non-emergency CRITICO or overtemp enforces cmd_valvula = True and cmd_alarme = True.
    5. severity >= 1 enforces requer_relatorio = True.
    """
    wf = _coerce_window_features(wf)
    stat = stat_fb or evaluate_statistical_fallback(wf)

    raw_label = str(candidate.get("severidade", stat.severity_label))
    raw_sev_code = SEVERITY_LABEL_TO_CODE.get(raw_label, stat.severity_code)

    reasons: list[str] = []

    # Rule A: Monotonic Severity Floor
    final_sev_code = max(raw_sev_code, stat.severity_code)
    if final_sev_code > raw_sev_code:
        reasons.append("severity_downgrade_blocked")

    raw_cmds = candidate.get("comandos_atuadores", {})
    cmd_motor = bool(raw_cmds.get("cmd_motor", stat.cmd_motor))
    cmd_valvula = bool(raw_cmds.get("cmd_valvula", stat.cmd_valvula))
    cmd_alarme = bool(raw_cmds.get("cmd_alarme", stat.cmd_alarme))
    requer_relatorio = bool(candidate.get("requer_relatorio", final_sev_code >= 1))

    # Rule B: Emergency Safe State
    if wf.veto_emergencia or final_sev_code == 3:
        if cmd_motor:
            cmd_motor = False
            reasons.append("motor_veto_emergencia")
        if cmd_valvula:
            cmd_valvula = False
            reasons.append("valvula_veto_emergencia")
        if not cmd_alarme:
            cmd_alarme = True
            reasons.append("alarme_forcado_emergencia")

    # Rule C: Motor Safety Veto
    if cmd_motor:
        if wf.veto_sobretemp:
            cmd_motor = False
            reasons.append("motor_veto_sobretemp")
        elif wf.veto_nivel_sustentado:
            cmd_motor = False
            reasons.append("motor_veto_nivel_sustentado")
        elif wf.veto_watchdog:
            cmd_motor = False
            reasons.append("motor_veto_watchdog")
        elif final_sev_code >= 2:
            cmd_motor = False
            reasons.append("motor_veto_critico_ou_emergencia")

    # Rule D: Non-Emergency Critical / Overtemp Relief & Alarm
    if not wf.veto_emergencia and final_sev_code != 3:
        if final_sev_code == 2 or wf.veto_sobretemp:
            if not cmd_valvula:
                cmd_valvula = True
                reasons.append("valvula_forcada_critico_sobretemp")
            if not cmd_alarme:
                cmd_alarme = True
                reasons.append("alarme_forcado_critico_sobretemp")
        elif final_sev_code == 1 and wf.duty_nivel > 0.0 and not cmd_valvula:
            cmd_valvula = True
            reasons.append("valvula_preventiva_nivel_alto")

    # Rule E: Mandatory Report Policy
    if final_sev_code >= 1 and not requer_relatorio:
        requer_relatorio = True
        reasons.append("relatorio_forcado_severidade")

    diag_text = _clamp_str(
        str(candidate.get("diagnostico", "Diagnostico operacional PLC500 gerado.")),
        10,
        400,
    )
    causa_text = _clamp_str(
        str(candidate.get("causa_provavel", "Condicao operacional observada em janela 60s.")),
        5,
        250,
    )
    resumo_text = _clamp_str(
        str(
            candidate.get(
                "resumo_executivo",
                "Resumo executivo de diagnostico e atuacao segura no WEG PLC500.",
            )
        ),
        20,
        800,
    )

    if reasons:
        guard_note = f" [Guardiao de seguranca: {', '.join(reasons)}]"
        if len(resumo_text) + len(guard_note) <= 800:
            resumo_text = resumo_text + guard_note
        else:
            resumo_text = _clamp_str(resumo_text[: 800 - len(guard_note)] + guard_note, 20, 800)

    guarded_payload: dict[str, Any] = {
        "severidade": SEVERITY_CODE_TO_LABEL[final_sev_code],
        "diagnostico": diag_text,
        "causa_provavel": causa_text,
        "comandos_atuadores": {
            "cmd_motor": bool(cmd_motor),
            "cmd_valvula": bool(cmd_valvula),
            "cmd_alarme": bool(cmd_alarme),
        },
        "requer_relatorio": bool(requer_relatorio),
        "resumo_executivo": resumo_text,
    }
    validate_diagnostico_json_schema(guarded_payload)

    return GuardedDecision(
        diagnostico_payload=guarded_payload,
        guard_applied=bool(reasons),
        guard_reasons=reasons,
        raw_severity_code=raw_sev_code,
        statistical_severity_code=stat.severity_code,
        final_severity_code=final_sev_code,
    )

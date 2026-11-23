"""Unit tests for deterministic statistical fallback and Monotonicity Safety Guardian."""

from __future__ import annotations

import pytest

from plc500_ml.features import WindowFeatures, extract_features_from_summary
from plc500_ml.safety_rules import (
    apply_monotonicity_guard,
    build_fallback_diagnosis,
    evaluate_statistical_fallback,
)
from plc500_ml.schemas import validate_diagnostico_json_schema


def _wf(
    *,
    taxa_presenca_min: float = 12.0,
    duty_nivel: float = 0.0,
    duty_sobretemp: float = 0.0,
    di_emergencia_ok: bool = True,
    flag_emerg_travada: bool = False,
    flag_watchdog: bool = False,
    di_sobretemp: bool = False,
) -> WindowFeatures:
    return extract_features_from_summary(
        {
            "taxa_presenca_min": taxa_presenca_min,
            "duty_nivel": duty_nivel,
            "duty_sobretemp": duty_sobretemp,
            "di_emergencia_ok": di_emergencia_ok,
            "flag_emerg_travada": flag_emerg_travada,
            "flag_watchdog": flag_watchdog,
            "di_sobretemp": di_sobretemp,
        }
    )


def test_deterministic_fallback_severity_matrix() -> None:
    # 0 = NORMAL
    fb_norm = evaluate_statistical_fallback(_wf())
    assert fb_norm.severity_code == 0
    assert (fb_norm.cmd_motor, fb_norm.cmd_valvula, fb_norm.cmd_alarme) == (True, False, False)
    assert fb_norm.requer_relatorio is False
    assert fb_norm.acionar_llm is False
    assert evaluate_statistical_fallback(_wf(), ciclo_sumario_horario=True).acionar_llm is True

    # 1 = ATENCAO (cadence drift +/-2 sigma OR intermittent high level 0 < D2 < 0.30)
    fb_att_drift_pos = evaluate_statistical_fallback(_wf(taxa_presenca_min=17.5))
    fb_att_drift_neg = evaluate_statistical_fallback(_wf(taxa_presenca_min=6.5))
    fb_att_lvl = evaluate_statistical_fallback(_wf(duty_nivel=0.15))
    for fb in (fb_att_drift_pos, fb_att_drift_neg, fb_att_lvl):
        assert fb.severity_code == 1
        assert (fb.cmd_motor, fb.cmd_valvula, fb.cmd_alarme) == (True, True, False)
        assert fb.requer_relatorio is True
        assert fb.acionar_llm is True

    # 2 = CRITICO (chattering z>=3, blockage z<=-3, sustained level >=0.30, overtemp, watchdog)
    for wf_crit in (
        _wf(taxa_presenca_min=22.0),
        _wf(taxa_presenca_min=0.0),
        _wf(duty_nivel=0.30),
        _wf(duty_sobretemp=0.05),
        _wf(flag_watchdog=True),
    ):
        fb = evaluate_statistical_fallback(wf_crit)
        assert fb.severity_code == 2
        assert (fb.cmd_motor, fb.cmd_valvula, fb.cmd_alarme) == (False, True, True)
        assert fb.requer_relatorio is True

    # 3 = EMERGENCIA overrides all other states
    for wf_emg in (
        _wf(di_emergencia_ok=False, duty_sobretemp=0.5, duty_nivel=0.8),
        _wf(flag_emerg_travada=True, flag_watchdog=True),
    ):
        fb = evaluate_statistical_fallback(wf_emg)
        assert fb.severity_code == 3
        assert (fb.cmd_motor, fb.cmd_valvula, fb.cmd_alarme) == (False, False, True)
        assert fb.requer_relatorio is True


@pytest.mark.parametrize(
    "wf_case,expected_sev,expected_cmds",
    [
        (_wf(di_emergencia_ok=False), 3, (False, False, True)),
        (_wf(flag_emerg_travada=True), 3, (False, False, True)),
        (_wf(duty_sobretemp=0.2, di_sobretemp=True), 2, (False, True, True)),
        (_wf(duty_nivel=0.45), 2, (False, True, True)),
        (_wf(flag_watchdog=True), 2, (False, True, True)),
        (_wf(taxa_presenca_min=0.0), 2, (False, True, True)),
    ],
)
def test_monotonicity_guardian_blocks_downgrades_and_unsafe_motor(
    wf_case: WindowFeatures,
    expected_sev: int,
    expected_cmds: tuple[bool, bool, bool],
) -> None:
    adversarial_candidate = {
        "severidade": "NORMAL",
        "diagnostico": "Sistema aparentemente normal segundo LLM descalibrado.",
        "causa_provavel": "Falso negativo proposital para teste de seguranca.",
        "comandos_atuadores": {
            "cmd_motor": True,
            "cmd_valvula": False,
            "cmd_alarme": False,
        },
        "requer_relatorio": False,
        "resumo_executivo": "Teste adversarial tentando ligar motor durante condicao insegura.",
    }

    guarded = apply_monotonicity_guard(adversarial_candidate, wf_case)
    validate_diagnostico_json_schema(guarded.diagnostico_payload)

    assert guarded.guard_applied is True
    assert guarded.final_severity_code == expected_sev
    cmds = guarded.diagnostico_payload["comandos_atuadores"]
    assert (cmds["cmd_motor"], cmds["cmd_valvula"], cmds["cmd_alarme"]) == expected_cmds
    assert cmds["cmd_motor"] is False
    assert guarded.diagnostico_payload["requer_relatorio"] is True


def test_monotonicity_guardian_allows_upward_ml_escalation_and_validates_fallback() -> None:
    wf_nom = _wf()
    escalated_candidate = {
        "severidade": "CRITICO",
        "diagnostico": "Padrao multivariado indica anomalia critica emergente.",
        "causa_provavel": "Desvio detectado pelo classificador ML.",
        "comandos_atuadores": {
            "cmd_motor": True,  # Unsafe under CRITICO -> guardian must block
            "cmd_valvula": False,
            "cmd_alarme": False,
        },
        "requer_relatorio": False,
        "resumo_executivo": "Escalacao ascendente permitida mantendo guardiao de seguranca.",
    }
    guarded = apply_monotonicity_guard(escalated_candidate, wf_nom)
    validate_diagnostico_json_schema(guarded.diagnostico_payload)
    assert guarded.final_severity_code == 2
    assert guarded.diagnostico_payload["comandos_atuadores"]["cmd_motor"] is False
    assert guarded.diagnostico_payload["comandos_atuadores"]["cmd_valvula"] is True
    assert guarded.diagnostico_payload["comandos_atuadores"]["cmd_alarme"] is True
    assert guarded.diagnostico_payload["requer_relatorio"] is True

    for wf_sample in (
        _wf(),
        _wf(duty_nivel=0.1),
        _wf(duty_sobretemp=0.3),
        _wf(di_emergencia_ok=False),
    ):
        fb_diag = build_fallback_diagnosis(wf_sample)
        validate_diagnostico_json_schema(fb_diag)

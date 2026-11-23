"""Sliding-window feature engineering with zero train/serve skew for WEG PLC500."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import Any

from plc500_ml.config import (
    BASELINE_EPS,
    BASELINE_MU,
    BASELINE_SIGMA,
    DUTY_NIVEL_CRITICAL_THRESHOLD,
    UINT16_MOD,
    WINDOW_SECONDS,
)


class EmptyWindowError(ValueError):
    """Raised when attempting to extract features from an empty telemetry window."""


# Exact 10 leakage-free features computable identically in BOTH raw 2 Hz mode
# (data_generator.py / POST /predict/raw-window) and summary mode (POST /predict).
MODEL_FEATURE_COLUMNS: tuple[str, ...] = (
    "taxa_presenca_min",
    "zscore_presenca",
    "abs_zscore_presenca",
    "duty_nivel",
    "duty_sobretemp",
    "chattering_index",
    "veto_emergencia",
    "veto_sobretemp",
    "veto_watchdog",
    "veto_nivel_sustentado",
)

# Extra raw counter deltas and duty ratios retained in WindowFeatures and
# window_features_60s.csv for OT/IT auditability (not fed into ML X).
AUDIT_FEATURE_COLUMNS: tuple[str, ...] = (
    "delta_cnt_presenca",
    "delta_cnt_nivel",
    "delta_cnt_sobretemp",
    "delta_cnt_emergencia",
    "duty_presenca",
    "duty_emerg_ok",
    "duty_emerg_travada",
    "duty_watchdog",
)

# Columns strictly forbidden from ML feature vectors to prevent target or
# PLC-state leakage.
FORBIDDEN_LEAKAGE_COLUMNS: frozenset[str] = frozenset(
    {
        "severity",
        "scenario",
        "severidade_estatistica",
        "estado_clp",
        "cod_intertrav",
        "mascara_do_real",
        "split",
        "window_id",
        "window_start_s",
        "window_end_s",
        "raw_start_idx",
        "raw_end_idx",
        "timestamp_start",
        "timestamp_end",
        "amostras_k",
        "samples_k",
        "time_s",
        "sample_idx",
        "lambda1",
        "z1",
        "taxa_presenca",
        "z_presenca",
    }
)


@dataclass(frozen=True, slots=True)
class WindowFeatures:
    """Extracted sliding-window telemetry features and audit metrics."""

    amostras_k: int
    window_seconds: float
    taxa_presenca_min: float
    zscore_presenca: float
    abs_zscore_presenca: float
    duty_nivel: float
    duty_sobretemp: float
    chattering_index: float
    veto_emergencia: bool
    veto_sobretemp: bool
    veto_watchdog: bool
    veto_nivel_sustentado: bool
    di_emergencia_ok: bool
    flag_emerg_travada: bool
    flag_watchdog: bool
    di_sobretemp: bool
    di_nivel_alto: bool
    delta_cnt_presenca: int = 0
    delta_cnt_nivel: int = 0
    delta_cnt_sobretemp: int = 0
    delta_cnt_emergencia: int = 0
    duty_presenca: float = 0.0
    duty_emerg_ok: float = 1.0
    duty_emerg_travada: float = 0.0
    duty_watchdog: float = 0.0

    def to_model_vector(self) -> list[float]:
        """Return the exact 10-element feature vector in MODEL_FEATURE_COLUMNS order."""
        return features_to_vector(self)

    def to_model_dict(self) -> dict[str, float]:
        """Return a mapping of MODEL_FEATURE_COLUMNS -> float."""
        return {
            "taxa_presenca_min": float(self.taxa_presenca_min),
            "zscore_presenca": float(self.zscore_presenca),
            "abs_zscore_presenca": float(self.abs_zscore_presenca),
            "duty_nivel": float(self.duty_nivel),
            "duty_sobretemp": float(self.duty_sobretemp),
            "chattering_index": float(self.chattering_index),
            "veto_emergencia": float(int(self.veto_emergencia)),
            "veto_sobretemp": float(int(self.veto_sobretemp)),
            "veto_watchdog": float(int(self.veto_watchdog)),
            "veto_nivel_sustentado": float(int(self.veto_nivel_sustentado)),
        }

    def to_dict(self) -> dict[str, Any]:
        """Return full serializable feature dictionary including audit metrics."""
        return asdict(self)


def delta_mod16(atual: int, anterior: int, *, strict: bool = True) -> int:
    """Compute wrap-around-safe 16-bit unsigned counter difference.

    Matches Node-RED reference JS: (atual - anterior + 65536) % 65536.
    """
    if isinstance(atual, bool) or isinstance(anterior, bool):
        raise TypeError("Counter values must be integers, not booleans.")
    if not isinstance(atual, int) or not isinstance(anterior, int):
        raise TypeError("Counter values must be integers.")

    if strict:
        if not (0 <= atual < UINT16_MOD) or not (0 <= anterior < UINT16_MOD):
            raise ValueError(
                f"UINT16 counters must be in [0, 65535]; got atual={atual}, anterior={anterior}"
            )
    else:
        atual &= 0xFFFF
        anterior &= 0xFFFF

    return (atual - anterior + UINT16_MOD) % UINT16_MOD


deltaMod16 = delta_mod16


def compute_lambda(event_count: int, window_s: float = WINDOW_SECONDS) -> float:
    """Compute arrival rate lambda_i(W) = (N_i(W) / W) * 60 [events/min]."""
    if window_s <= 0.0:
        raise ValueError(f"window_s must be positive, got {window_s}")
    if event_count < 0:
        raise ValueError(f"event_count must be non-negative, got {event_count}")
    return (float(event_count) / float(window_s)) * 60.0


def compute_z_score(
    rate_per_min: float,
    mu: float = BASELINE_MU,
    sigma: float = BASELINE_SIGMA,
    eps: float = BASELINE_EPS,
) -> float:
    """Compute regularized standardized anomaly score z_i = (lambda_i - mu) / (sigma + eps)."""
    if sigma < 0.0:
        raise ValueError(f"sigma must be non-negative, got {sigma}")
    if eps <= 0.0:
        raise ValueError(f"eps must be strictly positive, got {eps}")
    return (float(rate_per_min) - float(mu)) / (float(sigma) + float(eps))


compute_zscore = compute_z_score
zscore = compute_z_score


def compute_chattering_index_raw(
    di_presenca_seq: Sequence[bool],
    cnt_presenca_seq: Sequence[int],
    taxa_presenca_min: float,
    mu: float = BASELINE_MU,
    sigma: float = BASELINE_SIGMA,
) -> float:
    """Compute bounded [0.0, 1.0] chattering index from raw 2 Hz telemetry.

    Separates high-frequency oscillation/chattering (high index near 1.0)
    from conveyor blockage/stall (index 0.0).
    """
    n_samples = len(di_presenca_seq)
    if n_samples <= 1:
        return 0.0

    transitions = sum(
        1 for k in range(1, n_samples) if bool(di_presenca_seq[k]) != bool(di_presenca_seq[k - 1])
    )
    transition_density = transitions / float(n_samples - 1)

    step_deltas = [
        delta_mod16(int(cnt_presenca_seq[k]), int(cnt_presenca_seq[k - 1]))
        for k in range(1, n_samples)
    ]
    multi_pulse_ratio = sum(1 for d in step_deltas if d >= 2) / float(n_samples - 1)

    rate_excess = min(1.0, max(0.0, float(taxa_presenca_min) - float(mu)) / (4.0 * max(sigma, 0.5)))
    osc_component = max(transition_density, multi_pulse_ratio)
    idx = 0.5 * osc_component + 0.5 * max(multi_pulse_ratio, rate_excess)
    return float(min(1.0, max(0.0, idx)))


def compute_chattering_index_summary(
    taxa_presenca_min: float,
    mu: float = BASELINE_MU,
    sigma: float = BASELINE_SIGMA,
) -> float:
    """Estimate bounded [0.0, 1.0] chattering index from summary arrival rate."""
    if taxa_presenca_min <= 0.0:
        return 0.0
    transition_density_est = min(1.0, max(0.0, float(taxa_presenca_min)) / 60.0)
    rate_excess = min(1.0, max(0.0, float(taxa_presenca_min) - float(mu)) / (4.0 * max(sigma, 0.5)))
    idx = 0.5 * transition_density_est + 0.5 * rate_excess
    return float(min(1.0, max(0.0, idx)))


def _get_field(sample: Any, name: str, default: Any = None) -> Any:
    if isinstance(sample, Mapping):
        return sample.get(name, default)
    return getattr(sample, name, default)


def extract_features_from_raw_window(
    samples: Sequence[Mapping[str, Any] | Any],
    *,
    window_s: float = WINDOW_SECONDS,
    mu: float = BASELINE_MU,
    sigma: float = BASELINE_SIGMA,
    eps: float = BASELINE_EPS,
) -> WindowFeatures:
    """Extract sliding-window features from a sequence of 2 Hz telemetry samples."""
    k_samples = len(samples)
    if k_samples == 0:
        raise EmptyWindowError("Raw telemetry window is empty (amostras_k=0).")
    if window_s <= 0.0:
        raise ValueError(f"window_s must be positive, got {window_s}")

    inicio = samples[0]
    fim = samples[-1]

    di_presenca_seq = [bool(_get_field(s, "di_presenca", False)) for s in samples]
    di_nivel_seq = [bool(_get_field(s, "di_nivel_alto", False)) for s in samples]
    di_sobretemp_seq = [bool(_get_field(s, "di_sobretemp", False)) for s in samples]
    di_emerg_ok_seq = [bool(_get_field(s, "di_emergencia_ok", True)) for s in samples]
    flag_emerg_trav_seq = [bool(_get_field(s, "flag_emerg_travada", False)) for s in samples]
    flag_watchdog_seq = [bool(_get_field(s, "flag_watchdog", False)) for s in samples]
    cnt_presenca_seq = [int(_get_field(s, "cnt_presenca", 0)) for s in samples]

    if k_samples == 1:
        delta_cnt_presenca = 0
        delta_cnt_nivel = 0
        delta_cnt_sobretemp = 0
        delta_cnt_emergencia = 0
    else:
        delta_cnt_presenca = delta_mod16(
            int(_get_field(fim, "cnt_presenca", 0)),
            int(_get_field(inicio, "cnt_presenca", 0)),
        )
        delta_cnt_nivel = delta_mod16(
            int(_get_field(fim, "cnt_nivel_alto", 0)),
            int(_get_field(inicio, "cnt_nivel_alto", 0)),
        )
        delta_cnt_sobretemp = delta_mod16(
            int(_get_field(fim, "cnt_sobretemp", 0)),
            int(_get_field(inicio, "cnt_sobretemp", 0)),
        )
        delta_cnt_emergencia = delta_mod16(
            int(_get_field(fim, "cnt_emergencia", 0)),
            int(_get_field(inicio, "cnt_emergencia", 0)),
        )

    taxa_presenca_min = compute_lambda(delta_cnt_presenca, window_s=window_s)
    zscore_presenca = compute_z_score(taxa_presenca_min, mu=mu, sigma=sigma, eps=eps)
    abs_zscore_presenca = abs(zscore_presenca)

    duty_presenca = sum(1 for v in di_presenca_seq if v) / float(k_samples)
    duty_nivel = sum(1 for v in di_nivel_seq if v) / float(k_samples)
    duty_sobretemp = sum(1 for v in di_sobretemp_seq if v) / float(k_samples)
    duty_emerg_ok = sum(1 for v in di_emerg_ok_seq if v) / float(k_samples)
    duty_emerg_travada = sum(1 for v in flag_emerg_trav_seq if v) / float(k_samples)
    duty_watchdog = sum(1 for v in flag_watchdog_seq if v) / float(k_samples)

    chattering_index = compute_chattering_index_raw(
        di_presenca_seq=di_presenca_seq,
        cnt_presenca_seq=cnt_presenca_seq,
        taxa_presenca_min=taxa_presenca_min,
        mu=mu,
        sigma=sigma,
    )

    fim_di_emerg_ok = di_emerg_ok_seq[-1]
    fim_flag_emerg_trav = flag_emerg_trav_seq[-1]
    fim_flag_watchdog = flag_watchdog_seq[-1]
    fim_di_sobretemp = di_sobretemp_seq[-1]
    fim_di_nivel_alto = di_nivel_seq[-1]

    veto_emergencia = bool(fim_flag_emerg_trav or (not fim_di_emerg_ok))
    veto_sobretemp = bool(duty_sobretemp > 0.0 or fim_di_sobretemp)
    veto_watchdog = bool(fim_flag_watchdog)
    veto_nivel_sustentado = bool(duty_nivel >= DUTY_NIVEL_CRITICAL_THRESHOLD)

    return WindowFeatures(
        amostras_k=k_samples,
        window_seconds=float(window_s),
        taxa_presenca_min=float(taxa_presenca_min),
        zscore_presenca=float(zscore_presenca),
        abs_zscore_presenca=float(abs_zscore_presenca),
        duty_nivel=float(duty_nivel),
        duty_sobretemp=float(duty_sobretemp),
        chattering_index=float(chattering_index),
        veto_emergencia=veto_emergencia,
        veto_sobretemp=veto_sobretemp,
        veto_watchdog=veto_watchdog,
        veto_nivel_sustentado=veto_nivel_sustentado,
        di_emergencia_ok=fim_di_emerg_ok,
        flag_emerg_travada=fim_flag_emerg_trav,
        flag_watchdog=fim_flag_watchdog,
        di_sobretemp=fim_di_sobretemp,
        di_nivel_alto=fim_di_nivel_alto,
        delta_cnt_presenca=delta_cnt_presenca,
        delta_cnt_nivel=delta_cnt_nivel,
        delta_cnt_sobretemp=delta_cnt_sobretemp,
        delta_cnt_emergencia=delta_cnt_emergencia,
        duty_presenca=float(duty_presenca),
        duty_emerg_ok=float(duty_emerg_ok),
        duty_emerg_travada=float(duty_emerg_travada),
        duty_watchdog=float(duty_watchdog),
    )


def extract_window_features(
    samples: Sequence[Mapping[str, Any] | Any],
    *,
    window_s: float = WINDOW_SECONDS,
    mu: float = BASELINE_MU,
    sigma: float = BASELINE_SIGMA,
    eps: float = BASELINE_EPS,
) -> WindowFeatures:
    """Extract window features from raw 2 Hz samples, returning a safe default on empty input."""
    if len(samples) == 0:
        z0 = compute_z_score(0.0, mu=mu, sigma=sigma, eps=eps)
        return WindowFeatures(
            amostras_k=0,
            window_seconds=float(window_s),
            taxa_presenca_min=0.0,
            zscore_presenca=float(z0),
            abs_zscore_presenca=abs(float(z0)),
            duty_nivel=0.0,
            duty_sobretemp=0.0,
            chattering_index=0.0,
            veto_emergencia=False,
            veto_sobretemp=False,
            veto_watchdog=False,
            veto_nivel_sustentado=False,
            di_emergencia_ok=True,
            flag_emerg_travada=False,
            flag_watchdog=False,
            di_sobretemp=False,
            di_nivel_alto=False,
        )
    return extract_features_from_raw_window(
        samples,
        window_s=window_s,
        mu=mu,
        sigma=sigma,
        eps=eps,
    )


compute_window_features = extract_window_features
extract_features = extract_window_features


def extract_features_from_summary(
    summary: Mapping[str, Any] | Any,
    *,
    mu: float | None = None,
    sigma: float | None = None,
    eps: float | None = None,
) -> WindowFeatures:
    """Extract identical 10-element model features from a Node-RED summary payload."""
    if isinstance(summary, WindowFeatures):
        return summary

    amostras_k = int(_get_field(summary, "amostras_k", 120))
    if amostras_k <= 0:
        raise EmptyWindowError("Summary window has amostras_k <= 0.")

    window_s = float(
        _get_field(summary, "window_seconds", _get_field(summary, "window_s", WINDOW_SECONDS))
    )
    if window_s <= 0.0:
        raise ValueError(f"window_seconds must be positive, got {window_s}")

    eff_mu = float(
        mu
        if mu is not None
        else _get_field(
            summary, "baseline_mu", _get_field(summary, "baseline_mu_presenca", BASELINE_MU)
        )
    )
    eff_sigma = float(
        sigma
        if sigma is not None
        else _get_field(
            summary,
            "baseline_sigma",
            _get_field(summary, "baseline_sigma_presenca", BASELINE_SIGMA),
        )
    )
    eff_eps = float(eps if eps is not None else _get_field(summary, "zscore_eps", BASELINE_EPS))

    raw_z = _get_field(
        summary,
        "zscore_presenca",
        _get_field(
            summary,
            "z_score",
            _get_field(summary, "z1", _get_field(summary, "z_presenca", None)),
        ),
    )

    raw_rate = _get_field(
        summary,
        "taxa_presenca_min",
        _get_field(
            summary,
            "lambda1",
            _get_field(summary, "taxa_presenca", _get_field(summary, "rate_presenca", None)),
        ),
    )
    if raw_rate is None:
        n_presenca = _get_field(summary, "n_presenca", _get_field(summary, "delta_presenca", None))
        if n_presenca is not None:
            taxa_presenca_min = compute_lambda(int(n_presenca), window_s=window_s)
        elif raw_z is not None:
            taxa_presenca_min = max(0.0, eff_mu + float(raw_z) * (eff_sigma + eff_eps))
        else:
            raise ValueError("Summary must provide taxa_presenca_min (or lambda1 / n_presenca).")
    else:
        taxa_presenca_min = float(raw_rate)
        if taxa_presenca_min < 0.0:
            if raw_z is not None:
                taxa_presenca_min = 0.0
            else:
                raise ValueError(f"taxa_presenca_min must be non-negative, got {taxa_presenca_min}")

    duty_nivel = float(
        _get_field(summary, "duty_nivel", _get_field(summary, "duty_nivel_alto", 0.0))
    )
    duty_sobretemp = float(
        _get_field(
            summary,
            "duty_sobretemp",
            _get_field(summary, "duty_temp", 0.0),
        )
    )
    if not (0.0 <= duty_nivel <= 1.0):
        raise ValueError(f"duty_nivel must be in [0.0, 1.0], got {duty_nivel}")
    if not (0.0 <= duty_sobretemp <= 1.0):
        raise ValueError(f"duty_sobretemp must be in [0.0, 1.0], got {duty_sobretemp}")

    if raw_z is None:
        zscore_presenca = compute_z_score(
            taxa_presenca_min, mu=eff_mu, sigma=eff_sigma, eps=eff_eps
        )
    else:
        zscore_presenca = float(raw_z)
    abs_zscore_presenca = abs(zscore_presenca)

    raw_chat = _get_field(summary, "chattering_index", None)
    if raw_chat is None:
        chattering_index = compute_chattering_index_summary(
            taxa_presenca_min, mu=eff_mu, sigma=eff_sigma
        )
    else:
        chattering_index = float(min(1.0, max(0.0, float(raw_chat))))

    raw_emerg_ok = _get_field(
        summary,
        "di_emergencia_ok",
        _get_field(summary, "emergencia_ok", None),
    )
    raw_emerg_trav = _get_field(
        summary,
        "flag_emerg_travada",
        _get_field(summary, "emergencia_ativa", _get_field(summary, "emerg_ativa", None)),
    )
    di_emergencia_ok = True if raw_emerg_ok is None else bool(raw_emerg_ok)
    flag_emerg_travada = False if raw_emerg_trav is None else bool(raw_emerg_trav)
    flag_watchdog = bool(
        _get_field(
            summary,
            "flag_watchdog",
            _get_field(
                summary, "watchdog_ativo", _get_field(summary, "flag_watchdog_ativo", False)
            ),
        )
    )
    di_sobretemp = bool(_get_field(summary, "di_sobretemp", duty_sobretemp > 0.0))
    di_nivel_alto = bool(_get_field(summary, "di_nivel_alto", duty_nivel > 0.0))

    veto_emergencia = bool(flag_emerg_travada or (not di_emergencia_ok))
    veto_sobretemp = bool(duty_sobretemp > 0.0 or di_sobretemp)
    veto_watchdog = bool(flag_watchdog)
    veto_nivel_sustentado = bool(duty_nivel >= DUTY_NIVEL_CRITICAL_THRESHOLD)

    return WindowFeatures(
        amostras_k=amostras_k,
        window_seconds=window_s,
        taxa_presenca_min=taxa_presenca_min,
        zscore_presenca=zscore_presenca,
        abs_zscore_presenca=abs_zscore_presenca,
        duty_nivel=duty_nivel,
        duty_sobretemp=duty_sobretemp,
        chattering_index=chattering_index,
        veto_emergencia=veto_emergencia,
        veto_sobretemp=veto_sobretemp,
        veto_watchdog=veto_watchdog,
        veto_nivel_sustentado=veto_nivel_sustentado,
        di_emergencia_ok=di_emergencia_ok,
        flag_emerg_travada=flag_emerg_travada,
        flag_watchdog=flag_watchdog,
        di_sobretemp=di_sobretemp,
        di_nivel_alto=di_nivel_alto,
        duty_emerg_ok=1.0 if di_emergencia_ok else 0.0,
        duty_emerg_travada=1.0 if flag_emerg_travada else 0.0,
        duty_watchdog=1.0 if flag_watchdog else 0.0,
    )


def features_to_vector(wf: WindowFeatures) -> list[float]:
    """Convert WindowFeatures into the canonical 10-element ML feature vector."""
    return [
        float(wf.taxa_presenca_min),
        float(wf.zscore_presenca),
        float(wf.abs_zscore_presenca),
        float(wf.duty_nivel),
        float(wf.duty_sobretemp),
        float(wf.chattering_index),
        float(int(wf.veto_emergencia)),
        float(int(wf.veto_sobretemp)),
        float(int(wf.veto_watchdog)),
        float(int(wf.veto_nivel_sustentado)),
    ]

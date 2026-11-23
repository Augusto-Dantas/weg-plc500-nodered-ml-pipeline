"""Opaque-box E2E test helpers, lazy Modbus/OpenAPI adapters, and validator re-exports."""

from __future__ import annotations

import inspect
import json
import re
import socket
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

from tests_e2e.validators import (
    SExprParseError,
    ValidationResult,
    assert_nodered_flows_contract,
    assert_plc500_sql_contract,
    check_env_example_placeholders,
    check_forbidden_tracked_paths,
    git_check_ignore,
    git_tracked_files,
    load_diagnostico_schema,
    make_valid_diagnosis,
    parse_sexpr,
    parse_st_declarations,
    scan_text_for_secrets,
    strip_st_comments,
    validate_compose_file,
    validate_debounce_counter,
    validate_diagnostico_json,
    validate_js_syntax,
    validate_kicad8_sch,
    validate_makefile,
    validate_nodered_flows,
    validate_pdf_diagram,
    validate_plcopen_xml,
    validate_png_diagram,
    validate_postgres_ddl,
    validate_safety_interlock,
    validate_st_csv_contract,
    validate_svg_diagram,
    validate_watchdog_order,
    verify_overleaf_zip_parity,
)

__all__ = [
    "ModbusBenchClient",
    "SExprParseError",
    "SimulatorControlAdapter",
    "ValidationResult",
    "assert_nodered_flows_contract",
    "assert_plc500_sql_contract",
    "build_predict_feature_payload",
    "build_raw_window_payload",
    "check_env_example_placeholders",
    "check_forbidden_tracked_paths",
    "decode_do_mask",
    "delta_mod16",
    "discover_callable",
    "ensure_python_pkg_on_path",
    "find_free_port",
    "git_check_ignore",
    "git_tracked_files",
    "http_json_request",
    "load_diagnostico_schema",
    "make_nominal_raw_samples",
    "make_valid_diagnosis",
    "parse_sexpr",
    "parse_st_declarations",
    "scan_text_for_secrets",
    "strip_st_comments",
    "validate_compose_file",
    "validate_debounce_counter",
    "validate_diagnostico_json",
    "validate_js_syntax",
    "validate_kicad8_sch",
    "validate_makefile",
    "validate_nodered_flows",
    "validate_pdf_diagram",
    "validate_plcopen_xml",
    "validate_png_diagram",
    "validate_postgres_ddl",
    "validate_safety_interlock",
    "validate_st_csv_contract",
    "validate_svg_diagram",
    "validate_watchdog_order",
    "verify_overleaf_zip_parity",
    "wait_for_http",
    "wait_for_tcp",
]


def ensure_python_pkg_on_path(repo_root: Path) -> Path:
    """Ensure ``<repo_root>/python`` is on ``sys.path`` for lazy M3 imports."""
    py_dir = repo_root / "python"
    py_str = str(py_dir)
    if py_str not in sys.path:
        sys.path.insert(0, py_str)
    return py_dir


def find_free_port() -> int:
    """Bind an ephemeral localhost TCP port on port 0 and return the assigned port number."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def wait_for_tcp(
    host: str,
    port: int,
    timeout: float = 12.0,
    proc: Any = None,
    poll_interval: float = 0.05,
) -> None:
    """Poll TCP connectivity to ``(host, port)`` until reachable or ``timeout``."""
    deadline = time.monotonic() + timeout
    last_err: Exception | None = None
    while time.monotonic() < deadline:
        if proc is not None and proc.poll() is not None:
            out = ""
            if proc.stdout is not None:
                out = proc.stdout.read().decode("utf-8", errors="replace")
            raise RuntimeError(
                f"Subprocess exited early with code {proc.returncode} before binding "
                f"{host}:{port}.\nOutput:\n{out}"
            )
        try:
            with socket.create_connection((host, port), timeout=0.3):
                return
        except OSError as exc:
            last_err = exc
            time.sleep(poll_interval)
    raise TimeoutError(f"TCP {host}:{port} not reachable within {timeout}s ({last_err})")


def http_json_request(
    url: str,
    method: str = "GET",
    payload: Any = None,
    timeout: float = 5.0,
) -> tuple[int, Any]:
    """Perform stdlib HTTP JSON request returning ``(status_code, parsed_json_or_text)``."""
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            try:
                return int(resp.status), json.loads(raw)
            except json.JSONDecodeError:
                return int(resp.status), raw
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            return int(exc.code), json.loads(raw)
        except json.JSONDecodeError:
            return int(exc.code), raw


def wait_for_http(
    url: str,
    timeout: float = 12.0,
    expected_status: int = 200,
    proc: Any = None,
    poll_interval: float = 0.1,
) -> Any:
    """Poll HTTP endpoint until ``expected_status`` or raise ``TimeoutError``."""
    deadline = time.monotonic() + timeout
    last_exc: Exception | None = None
    while time.monotonic() < deadline:
        if proc is not None and proc.poll() is not None:
            out = ""
            if proc.stdout is not None:
                out = proc.stdout.read().decode("utf-8", errors="replace")
            raise RuntimeError(
                f"Subprocess exited early with code {proc.returncode} before {url} ready.\n"
                f"Output:\n{out}"
            )
        try:
            status, body = http_json_request(url, timeout=1.0)
            if status == expected_status:
                return body
        except (urllib.error.URLError, OSError) as exc:
            last_exc = exc
        time.sleep(poll_interval)
    raise TimeoutError(f"HTTP {url} did not return {expected_status} within {timeout}s ({last_exc})")


def delta_mod16(atual: int, anterior: int) -> int:
    """Compute unsigned 16-bit modular counter delta."""
    return (int(atual) - int(anterior) + 65536) % 65536


def decode_do_mask(mask: int) -> dict[str, bool]:
    """Decode ``wMascara_DO_Real`` 4-bit integer into ``DO0..DO3`` booleans."""
    val = int(mask)
    return {
        "DO0": bool(val & 0b0001),
        "DO1": bool(val & 0b0010),
        "DO2": bool(val & 0b0100),
        "DO3": bool(val & 0b1000),
    }


def discover_callable(module: Any, candidate_names: tuple[str, ...]) -> Callable[..., Any]:
    """Discover a contracted public callable on ``module`` by candidate names."""
    for name in candidate_names:
        fn = getattr(module, name, None)
        if callable(fn):
            return fn
    available = [attr for attr in dir(module) if not attr.startswith("_")]
    raise AssertionError(
        f"None of {candidate_names} found on {getattr(module, '__name__', module)}; "
        f"available={available}"
    )


class ModbusBenchClient:
    """Version-agnostic Modbus TCP client wrapping ``pymodbus`` lazily."""

    def __init__(self, host: str = "127.0.0.1", port: int = 502, unit_id: int = 1) -> None:
        from pymodbus.client import ModbusTcpClient

        self.host = host
        self.port = port
        self.unit_id = unit_id
        self._client = ModbusTcpClient(host=host, port=port, timeout=2.0)
        if not self._client.connect():
            raise ConnectionError(f"Failed to connect ModbusTcpClient to {host}:{port}")

        params = inspect.signature(self._client.read_discrete_inputs).parameters
        if "device_id" in params:
            self._unit_kw = "device_id"
        elif "slave" in params:
            self._unit_kw = "slave"
        elif "unit" in params:
            self._unit_kw = "unit"
        else:
            self._unit_kw = ""

    def _kw(self) -> dict[str, int]:
        return {self._unit_kw: self.unit_id} if self._unit_kw else {}

    def close(self) -> None:
        self._client.close()

    def read_dis(self, address: int = 0, count: int = 6) -> list[bool]:
        resp = self._client.read_discrete_inputs(address, count=count, **self._kw())
        assert not resp.isError(), f"FC02 read_discrete_inputs error: {resp}"
        return [bool(b) for b in resp.bits[:count]]

    def read_coils(self, address: int = 0, count: int = 5) -> list[bool]:
        resp = self._client.read_coils(address, count=count, **self._kw())
        assert not resp.isError(), f"FC01 read_coils error: {resp}"
        return [bool(b) for b in resp.bits[:count]]

    def read_irs(self, address: int = 0, count: int = 8) -> list[int]:
        resp = self._client.read_input_registers(address, count=count, **self._kw())
        assert not resp.isError(), f"FC04 read_input_registers error: {resp}"
        return [int(r) for r in resp.registers[:count]]

    def read_hrs(self, address: int = 0, count: int = 4) -> list[int]:
        resp = self._client.read_holding_registers(address, count=count, **self._kw())
        assert not resp.isError(), f"FC03 read_holding_registers error: {resp}"
        return [int(r) for r in resp.registers[:count]]

    def write_coil(self, address: int, value: bool) -> None:
        resp = self._client.write_coil(address, bool(value), **self._kw())
        assert not resp.isError(), f"FC05 write_coil({address}, {value}) error: {resp}"

    def write_coils(self, address: int, values: list[bool]) -> None:
        resp = self._client.write_coils(address, [bool(v) for v in values], **self._kw())
        assert not resp.isError(), f"FC15 write_coils({address}, {values}) error: {resp}"

    def write_hr(self, address: int, value: int) -> None:
        resp = self._client.write_register(address, int(value), **self._kw())
        assert not resp.isError(), f"FC06 write_register({address}, {value}) error: {resp}"

    def write_hrs(self, address: int, values: list[int]) -> None:
        resp = self._client.write_registers(address, [int(v) for v in values], **self._kw())
        assert not resp.isError(), f"FC16 write_registers({address}, {values}) error: {resp}"

    def timed_read_irs(self, address: int = 0, count: int = 8) -> tuple[list[int], float]:
        t0 = time.perf_counter()
        vals = self.read_irs(address=address, count=count)
        return vals, (time.perf_counter() - t0) * 1000.0

    def timed_write_coils(self, address: int, values: list[bool]) -> float:
        t0 = time.perf_counter()
        self.write_coils(address=address, values=values)
        return (time.perf_counter() - t0) * 1000.0

    def snapshot(self) -> dict[str, Any]:
        dis = self.read_dis()
        coils = self.read_coils()
        irs = self.read_irs()
        hrs = self.read_hrs()
        return {
            "xDI_Presenca": dis[0],
            "xDI_NivelAlto": dis[1],
            "xDI_Sobretemp": dis[2],
            "xDI_EmergenciaOK": dis[3],
            "xFlag_EmergTravada": dis[4],
            "xFlag_WatchdogAtivo": dis[5],
            "xCmd_Motor": coils[0],
            "xCmd_Valvula": coils[1],
            "xCmd_Alarme": coils[2],
            "xCmd_ResetFalha": coils[3],
            "xCmd_HabRemoto": coils[4],
            "uiCnt_Presenca": irs[0],
            "uiCnt_NivelAlto": irs[1],
            "uiCnt_Sobretemp": irs[2],
            "uiCnt_Emergencia": irs[3],
            "uiEstado_CLP": irs[4],
            "wMascara_DO_Real": irs[5],
            "uiEcho_Heartbeat": irs[6],
            "uiCod_Intertrav": irs[7],
            "uiHeartbeat_Sup": hrs[0],
            "uiTimeout_WD_ms": hrs[1],
            "uiSeveridade_IA": hrs[2],
            "uiVersao_Mapa": hrs[3],
            "do": decode_do_mask(irs[5]),
            "dis": dis,
            "coils": coils,
            "irs": irs,
            "hrs": hrs,
        }

    def wait_until(
        self,
        predicate: Callable[[dict[str, Any]], bool],
        timeout: float = 2.0,
        poll_interval: float = 0.02,
    ) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        last_snap: dict[str, Any] = {}
        while time.monotonic() < deadline:
            last_snap = self.snapshot()
            if predicate(last_snap):
                return last_snap
            time.sleep(poll_interval)
        raise TimeoutError(
            f"ModbusBenchClient.wait_until timed out after {timeout}s; last_snap={last_snap}"
        )


def _resolve_openapi_schema(spec: dict[str, Any], schema_obj: dict[str, Any]) -> dict[str, Any]:
    if "$ref" in schema_obj:
        ref = str(schema_obj["$ref"])
        parts = ref.lstrip("#/").split("/")
        cur: Any = spec
        for part in parts:
            cur = cur[part]
        return cur if isinstance(cur, dict) else {}
    return schema_obj


class SimulatorControlAdapter:
    """Dynamic OpenAPI adapter for ``plc_simulator.py`` fault-injection HTTP API."""

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")
        status, spec = http_json_request(f"{self.base_url}/openapi.json", timeout=5.0)
        if status != 200 or not isinstance(spec, dict):
            raise AssertionError(
                f"Simulator fault-injection /openapi.json unreachable at {self.base_url}: "
                f"status={status}, body={spec}"
            )
        self.openapi = spec
        self.paths: dict[str, Any] = spec.get("paths", {})

    def _find_route(
        self, keywords: tuple[str, ...], methods: tuple[str, ...] = ("post", "put", "patch")
    ) -> tuple[str, str, dict[str, Any]] | None:
        for path, item in self.paths.items():
            if not isinstance(item, dict):
                continue
            low_path = path.lower()
            if any(kw in low_path for kw in keywords):
                for m in methods:
                    if m in item:
                        return path, m.upper(), item[m]
        return None

    def _body_properties(self, op_spec: dict[str, Any]) -> dict[str, Any]:
        rb = op_spec.get("requestBody") or {}
        content = rb.get("content") or {}
        app_json = content.get("application/json") or {}
        schema = _resolve_openapi_schema(self.openapi, app_json.get("schema") or {})
        props = schema.get("properties")
        return props if isinstance(props, dict) else {}

    def _map_di_payload(self, props: dict[str, Any], updates: dict[str, bool]) -> dict[str, Any]:
        aliases: dict[str, tuple[str, ...]] = {
            "di0": ("di0", "xdi_presenca", "presenca", "b1", "b1_senspresenca"),
            "di1": ("di1", "xdi_nivelalto", "nivel_alto", "b2", "b2_sensnivelalto"),
            "di2": ("di2", "xdi_sobretemp", "sobretemp", "b3", "b3_senssobretemp"),
            "di3": ("di3", "xdi_emergenciaok", "emergencia_ok", "b4", "b4_emergencia"),
        }
        payload: dict[str, Any] = {}
        low_props = {k.lower(): k for k in props}
        for chan, val in updates.items():
            matched_key = None
            for cand in aliases[chan]:
                if cand in low_props:
                    matched_key = low_props[cand]
                    break
            if matched_key is None:
                for p_low, p_orig in low_props.items():
                    if any(cand in p_low for cand in aliases[chan]):
                        matched_key = p_orig
                        break
            if matched_key is not None:
                payload[matched_key] = bool(val)
        return payload

    def set_inputs(
        self,
        *,
        di0: bool | None = None,
        di1: bool | None = None,
        di2: bool | None = None,
        di3: bool | None = None,
    ) -> Any:
        updates = {
            k: v
            for k, v in (("di0", di0), ("di1", di1), ("di2", di2), ("di3", di3))
            if v is not None
        }
        if not updates:
            return None

        # Prefer /inputs or /faults or /di route with JSON body
        for kw_group in (
            ("inputs", "input"),
            ("fault", "inject"),
            ("di", "sensor", "state", "override"),
        ):
            route = self._find_route(kw_group)
            if route is None:
                continue
            path, method, op_spec = route
            if "{" in path:
                # Path-parameter style e.g. /inputs/{channel}
                last_res = None
                for chan, val in updates.items():
                    filled = re.sub(r"\{[^}]+\}", chan, path)
                    status, body = http_json_request(
                        f"{self.base_url}{filled}",
                        method=method,
                        payload={"value": bool(val), "state": bool(val)},
                    )
                    assert 200 <= status < 300, f"{method} {filled} failed: {status} {body}"
                    last_res = body
                return last_res

            props = self._body_properties(op_spec)
            payload = self._map_di_payload(props, updates) if props else updates
            if payload:
                status, body = http_json_request(
                    f"{self.base_url}{path}", method=method, payload=payload
                )
                assert 200 <= status < 300, f"{method} {path}({payload}) failed: {status} {body}"
                return body

        # Secondary check: dedicated fault toggles like /fault/estop, /fault/overtemp
        raise AssertionError(
            f"Could not map set_inputs({updates}) onto simulator OpenAPI paths: "
            f"{list(self.paths)}"
        )

    def pulse_di(self, channel: str, duration_ms: int) -> None:
        chan = channel.lower()
        assert chan in ("di0", "di1", "di2", "di3"), f"Invalid channel {channel!r}"
        route = self._find_route(("pulse",))
        if route is not None:
            path, method, op_spec = route
            props = self._body_properties(op_spec)
            low_props = {k.lower(): k for k in props}
            chan_key = next(
                (low_props[k] for k in ("channel", "di", "input", "sensor") if k in low_props),
                "channel",
            )
            dur_key = next(
                (
                    low_props[k]
                    for k in ("duration_ms", "ms", "width_ms", "duration")
                    if k in low_props
                ),
                "duration_ms",
            )
            payload: dict[str, Any] = {chan_key: chan, dur_key: int(duration_ms)}
            if "value" in low_props:
                payload[low_props["value"]] = chan != "di3"
            status, _body = http_json_request(
                f"{self.base_url}{path}", method=method, payload=payload
            )
            if 200 <= status < 300:
                return

        # Fallback: direct toggle via set_inputs
        active = chan != "di3"
        inactive = not active
        self.set_inputs(**{chan: active})
        time.sleep(max(0.005, duration_ms / 1000.0))
        self.set_inputs(**{chan: inactive})

    def reset_state(self) -> None:
        route = self._find_route(("reset",))
        if route is not None:
            path, method, _ = route
            status, _body = http_json_request(f"{self.base_url}{path}", method=method, payload={})
            if 200 <= status < 300:
                return
        self.set_inputs(di0=False, di1=False, di2=False, di3=True)

    def get_state(self) -> dict[str, Any]:
        for path, item in self.paths.items():
            if isinstance(item, dict) and "get" in item and any(
                kw in path.lower() for kw in ("state", "status", "inputs", "health")
            ):
                status, body = http_json_request(f"{self.base_url}{path}", method="GET")
                if status == 200 and isinstance(body, dict):
                    return body
        return {}


def make_nominal_raw_samples(
    count: int = 120,
    *,
    start_cnt_presenca: int = 100,
    delta_presenca: int = 12,
    nivel_alto_count: int = 0,
    sobretemp_count: int = 0,
    emergencia_ok: bool = True,
    flag_watchdog: bool = False,
) -> list[dict[str, Any]]:
    """Build ``count`` synthetic 2 Hz raw telemetry dicts for ``POST /predict/raw-window``."""
    samples: list[dict[str, Any]] = []
    for idx in range(count):
        if count > 1:
            cnt_p = (start_cnt_presenca + (delta_presenca * idx) // (count - 1)) % 65536
        else:
            cnt_p = start_cnt_presenca % 65536
        samples.append(
            {
                "timestamp_s": idx * 0.5,
                "di_presenca": (idx % 10) == 0,
                "di_nivel_alto": idx < nivel_alto_count,
                "di_sobretemp": idx < sobretemp_count,
                "di_emergencia_ok": bool(emergencia_ok),
                "flag_emerg_travada": not bool(emergencia_ok),
                "flag_watchdog": bool(flag_watchdog),
                "cnt_presenca": cnt_p,
                "cnt_nivel_alto": 1 if nivel_alto_count > 0 else 0,
                "cnt_sobretemp": 1 if sobretemp_count > 0 else 0,
                "cnt_emergencia": 0 if emergencia_ok else 1,
                "estado_clp": 4 if not emergencia_ok else (3 if flag_watchdog else 1),
                "mascara_do_real": 4 if not emergencia_ok else 9,
                "cod_intertrav": 1 if not emergencia_ok else (2 if sobretemp_count > 0 else 0),
            }
        )
    return samples


def _map_sample_fields(props: dict[str, Any], sample: dict[str, Any]) -> dict[str, Any]:
    if not props:
        return dict(sample)
    aliases: dict[str, tuple[str, ...]] = {
        "cnt_presenca": ("cnt_presenca", "uicnt_presenca", "presenca_cnt"),
        "cnt_nivel_alto": ("cnt_nivel_alto", "uicnt_nivelalto"),
        "cnt_sobretemp": ("cnt_sobretemp", "uicnt_sobretemp"),
        "cnt_emergencia": ("cnt_emergencia", "uicnt_emergencia"),
        "di_presenca": ("di_presenca", "xdi_presenca"),
        "di_nivel_alto": ("di_nivel_alto", "xdi_nivelalto", "nivel_alto"),
        "di_sobretemp": ("di_sobretemp", "xdi_sobretemp", "sobretemp"),
        "di_emergencia_ok": ("di_emergencia_ok", "xdi_emergenciaok", "emergencia_ok"),
        "flag_emerg_travada": ("flag_emerg_travada", "xflag_emergtravada", "emerg_travada"),
        "flag_watchdog": ("flag_watchdog", "xflag_watchdogativo", "flag_watchdog_ativo", "watchdog"),
        "estado_clp": ("estado_clp", "uiestado_clp"),
        "mascara_do_real": ("mascara_do_real", "wmascara_do_real"),
        "cod_intertrav": ("cod_intertrav", "uicod_intertrav"),
        "timestamp_s": ("timestamp_s", "timestamp", "ts"),
    }
    out: dict[str, Any] = {}
    low_props = {k.lower(): k for k in props}
    for canonical, val in sample.items():
        cands = aliases.get(canonical, (canonical,))
        target = next((low_props[c] for c in cands if c in low_props), None)
        if target is not None:
            out[target] = val
    # Fill any remaining unmapped keys from sample if schema didn't reject them
    return out or dict(sample)


def build_raw_window_payload(
    openapi_spec: dict[str, Any], samples: list[dict[str, Any]]
) -> Any:
    """Adapt canonical ``samples`` list to the exact ``/predict/raw-window`` OpenAPI schema."""
    paths = openapi_spec.get("paths") or {}
    op = (paths.get("/predict/raw-window") or {}).get("post") or {}
    rb = op.get("requestBody") or {}
    app_json = (rb.get("content") or {}).get("application/json") or {}
    schema = _resolve_openapi_schema(openapi_spec, app_json.get("schema") or {})

    if schema.get("type") == "array":
        item_schema = _resolve_openapi_schema(openapi_spec, schema.get("items") or {})
        item_props = item_schema.get("properties") or {}
        return [_map_sample_fields(item_props, s) for s in samples]

    props = schema.get("properties") or {}
    for key in ("samples", "amostras", "window", "rows", "telemetry", "data"):
        if key in props:
            arr_schema = _resolve_openapi_schema(openapi_spec, props[key])
            item_schema = _resolve_openapi_schema(openapi_spec, arr_schema.get("items") or {})
            item_props = item_schema.get("properties") or {}
            return {key: [_map_sample_fields(item_props, s) for s in samples]}

    # Fallback: first array property
    for key, prop_schema in props.items():
        resolved = _resolve_openapi_schema(openapi_spec, prop_schema)
        if resolved.get("type") == "array":
            item_schema = _resolve_openapi_schema(openapi_spec, resolved.get("items") or {})
            item_props = item_schema.get("properties") or {}
            return {key: [_map_sample_fields(item_props, s) for s in samples]}

    return {"samples": samples}


def build_predict_feature_payload(
    openapi_spec: dict[str, Any], **canonical_features: Any
) -> dict[str, Any]:
    """Adapt canonical window feature names to the exact ``POST /predict`` OpenAPI schema."""
    paths = openapi_spec.get("paths") or {}
    op = (paths.get("/predict") or {}).get("post") or {}
    rb = op.get("requestBody") or {}
    app_json = (rb.get("content") or {}).get("application/json") or {}
    schema = _resolve_openapi_schema(openapi_spec, app_json.get("schema") or {})
    props = schema.get("properties") or {}

    if not props:
        return dict(canonical_features)

    aliases: dict[str, tuple[str, ...]] = {
        "lambda1": ("lambda1", "taxa_presenca", "taxa_pecas_min", "rate_presenca"),
        "z1": ("z1", "z_presenca", "zscore_presenca", "z_score"),
        "duty_nivel": ("duty_nivel", "duty_nivel_alto", "dutynivel", "d2"),
        "duty_temp": ("duty_temp", "duty_sobretemp", "dutytemp", "d3"),
        "delta_presenca": ("delta_presenca", "n_presenca", "cnt_presenca_delta"),
        "delta_nivel_alto": ("delta_nivel_alto", "n_nivel_alto"),
        "delta_sobretemp": ("delta_sobretemp", "n_sobretemp"),
        "delta_emergencia": ("delta_emergencia", "n_emergencia"),
        "chattering_index": ("chattering_index", "indice_chattering", "chattering"),
        "emergencia_ok": ("emergencia_ok", "di_emergencia_ok", "xdi_emergenciaok"),
        "emergencia_ativa": ("emergencia_ativa", "emerg_ativa", "flag_emerg_travada"),
        "flag_watchdog": ("flag_watchdog", "watchdog_ativo", "flag_watchdog_ativo"),
    }

    low_props = {k.lower(): k for k in props}
    payload: dict[str, Any] = {}
    for canon_key, val in canonical_features.items():
        cands = aliases.get(canon_key, (canon_key,))
        target = next((low_props[c] for c in cands if c in low_props), None)
        if target is not None:
            payload[target] = val

    # Provide safe defaults for required fields if any weren't passed explicitly
    for req_key in schema.get("required") or []:
        if req_key not in payload:
            prop_s = props.get(req_key, {})
            if "default" in prop_s:
                payload[req_key] = prop_s["default"]
            elif prop_s.get("type") == "boolean":
                payload[req_key] = req_key.lower().endswith("ok")
            elif prop_s.get("type") in ("integer", "number"):
                payload[req_key] = 0
    return payload

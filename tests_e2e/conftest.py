"""Pytest configuration, marker registration, and dynamic E2E fixtures."""

from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from tests_e2e.helpers import (
    ModbusBenchClient,
    SimulatorControlAdapter,
    ensure_python_pkg_on_path,
    find_free_port,
    wait_for_http,
    wait_for_tcp,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


def pytest_configure(config: pytest.Config) -> None:
    """Register all E2E tier, area, milestone, and self-test pytest markers."""
    markers = (
        "tier1: Tier 1 feature coverage E2E tests",
        "tier2: Tier 2 boundary, corner-case, and negative validator E2E tests",
        "tier3: Tier 3 cross-feature pairwise and cross-artifact E2E tests",
        "tier4: Tier 4 real-world industrial scenarios (CT-01..CT-10, shift, acceptance)",
        "area_a: Area A — Monorepo migration, Git hygiene, toolchain, docs, CI",
        "area_b: Area B — OT Structured Text, PLCopen TC6 XML, Modbus CSV map",
        "area_c: Area C — Python ML pipeline, safety rules, FastAPI, Modbus simulator, datasets",
        "area_d: Area D — PostgreSQL DDL, JSON Schema, Node-RED flows/JS, Docker Compose, Makefile",
        "area_e: Area E — Hardware BOM, wiring table, CircuiTikZ diagrams, KiCad 8",
        "m6_final: Tests targeting M6-only deliverables (bilingual root READMEs, CI workflow)",
        "self_test: E2E infrastructure, lazy-import AST guard, and validator self-tests",
    )
    for line in markers:
        config.addinivalue_line("markers", line)


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return REPO_ROOT


@pytest.fixture(scope="session")
def docs_relatorio_dir(repo_root: Path) -> Path:
    return repo_root / "docs" / "relatorio"


@pytest.fixture(scope="session")
def plc_dir(repo_root: Path) -> Path:
    return repo_root / "plc"


@pytest.fixture(scope="session")
def python_dir(repo_root: Path) -> Path:
    return repo_root / "python"


@pytest.fixture(scope="session")
def data_dir(repo_root: Path) -> Path:
    return repo_root / "data"


@pytest.fixture(scope="session")
def node_red_dir(repo_root: Path) -> Path:
    return repo_root / "node-red"


@pytest.fixture(scope="session")
def hardware_dir(repo_root: Path) -> Path:
    return repo_root / "hardware"


@pytest.fixture(scope="session")
def diagnostico_schema(node_red_dir: Path) -> dict[str, Any]:
    schema_path = node_red_dir / "schemas" / "diagnostico_atuacao.schema.json"
    return json.loads(schema_path.read_text(encoding="utf-8"))


def _build_sim_command_and_env(
    root: Path, modbus_port: int, api_port: int
) -> tuple[list[str], dict[str, str]]:
    py_dir = root / "python"
    sim_script = py_dir / "plc500_ml" / "plc_simulator.py"
    if not sim_script.is_file():
        raise FileNotFoundError(f"Missing simulator module: {sim_script}")

    env = os.environ.copy()
    existing_pp = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = f"{py_dir}{os.pathsep}{existing_pp}" if existing_pp else str(py_dir)
    env["PYTHONUNBUFFERED"] = "1"
    env["PLC_SIM_HOST"] = "127.0.0.1"
    env["PLC_SIM_PORT"] = str(modbus_port)
    env["PLC_HOST"] = "127.0.0.1"
    env["PLC_PORT"] = str(modbus_port)
    env["PLC_UNIT_ID"] = "1"
    env["UNIT_ID"] = "1"
    env["PLC_SIM_API_HOST"] = "127.0.0.1"
    env["PLC_SIM_API_PORT"] = str(api_port)
    env["PLC_SIM_API_URL"] = f"http://127.0.0.1:{api_port}"

    cmd = [sys.executable, "-m", "plc500_ml.plc_simulator"]
    try:
        help_proc = subprocess.run(
            [*cmd, "--help"],
            check=False,
            cwd=str(root),
            env=env,
            capture_output=True,
            text=True,
            timeout=5.0,
        )
        help_text = f"{help_proc.stdout}\n{help_proc.stderr}"
    except (subprocess.SubprocessError, OSError):
        help_text = ""

    def has_flag(flag: str) -> bool:
        return bool(re.search(rf"(?<![\w-]){re.escape(flag)}(?![\w-])", help_text))

    for host_flag in ("--host", "--modbus-host"):
        if has_flag(host_flag):
            cmd.extend([host_flag, "127.0.0.1"])
            break
    for port_flag in ("--modbus-port", "--port"):
        if has_flag(port_flag):
            cmd.extend([port_flag, str(modbus_port)])
            break
    for api_host_flag in ("--api-host",):
        if has_flag(api_host_flag):
            cmd.extend([api_host_flag, "127.0.0.1"])
            break
    for api_port_flag in ("--api-port",):
        if has_flag(api_port_flag):
            cmd.extend([api_port_flag, str(api_port)])
            break

    return cmd, env


def _kill_process_group(proc: subprocess.Popen[bytes]) -> None:
    if proc.poll() is not None:
        return
    try:
        pgid = os.getpgid(proc.pid)
        os.killpg(pgid, signal.SIGTERM)
        proc.wait(timeout=5.0)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        try:
            pgid = os.getpgid(proc.pid)
            os.killpg(pgid, signal.SIGKILL)
            proc.wait(timeout=2.0)
        except ProcessLookupError:
            pass


def _start_plc_simulator(root: Path) -> dict[str, Any]:
    ensure_python_pkg_on_path(root)
    modbus_port = find_free_port()
    api_port = find_free_port()
    cmd, env = _build_sim_command_and_env(root, modbus_port, api_port)

    proc = subprocess.Popen(
        cmd,
        cwd=str(root),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    try:
        wait_for_tcp("127.0.0.1", modbus_port, timeout=12.0, proc=proc)
        api_base = f"http://127.0.0.1:{api_port}"
        wait_for_http(f"{api_base}/openapi.json", timeout=8.0, proc=proc)
        adapter = SimulatorControlAdapter(api_base)
    except Exception:
        _kill_process_group(proc)
        raise

    return {
        "proc": proc,
        "host": "127.0.0.1",
        "modbus_port": modbus_port,
        "api_port": api_port,
        "api_url": f"http://127.0.0.1:{api_port}",
        "adapter": adapter,
    }


def _reset_sim_state(sim_info: dict[str, Any]) -> ModbusBenchClient:
    adapter: SimulatorControlAdapter = sim_info["adapter"]
    client = ModbusBenchClient(host=sim_info["host"], port=sim_info["modbus_port"], unit_id=1)

    adapter.reset_state()
    client.write_hr(1, 5000)
    client.write_hr(2, 0)
    client.write_coil(4, False)
    client.write_coils(0, [False, False, False])

    # Clear any watchdog by pulsing heartbeat twice
    hrs = client.read_hrs()
    hb = (hrs[0] + 1) % 65536
    client.write_hr(0, hb)
    time.sleep(0.03)
    client.write_hr(0, (hb + 1) % 65536)

    # Clear any latched E-stop after ensuring DI3=1, DI2=0
    client.write_coil(3, True)
    time.sleep(0.03)
    client.write_coil(3, False)

    client.wait_until(
        lambda s: (
            s["uiEstado_CLP"] == 0
            and s["xFlag_EmergTravada"] is False
            and s["xFlag_WatchdogAtivo"] is False
        ),
        timeout=3.0,
    )
    return client


@pytest.fixture(scope="module")
def plc_sim_session(repo_root: Path) -> Iterator[dict[str, Any]]:
    """Module-scoped ``plc_simulator.py`` subprocess on ephemeral ports with killpg teardown."""
    sim_info = _start_plc_simulator(repo_root)
    try:
        yield sim_info
    finally:
        _kill_process_group(sim_info["proc"])


@pytest.fixture
def plc_sim(plc_sim_session: dict[str, Any]) -> Iterator[dict[str, Any]]:
    """Function-scoped clean handle to ``plc_simulator`` with isolated reset & Modbus client."""
    client = _reset_sim_state(plc_sim_session)
    try:
        yield {
            **plc_sim_session,
            "client": client,
        }
    finally:
        client.close()


@pytest.fixture
def fresh_plc_sim(repo_root: Path) -> Iterator[dict[str, Any]]:
    """Dedicated cold-boot ``plc_simulator.py`` subprocess for boot-default assertions."""
    sim_info = _start_plc_simulator(repo_root)
    client = ModbusBenchClient(host=sim_info["host"], port=sim_info["modbus_port"], unit_id=1)
    try:
        yield {
            **sim_info,
            "client": client,
        }
    finally:
        client.close()
        _kill_process_group(sim_info["proc"])


@pytest.fixture(scope="session")
def ml_api_client(repo_root: Path) -> Iterator[Any]:
    """Session-scoped FastAPI ``TestClient`` for ``plc500_ml.api`` (lazy-imported)."""
    ensure_python_pkg_on_path(repo_root)
    models_dir = repo_root / "python" / "models"
    if "ML_MODELS_DIR" not in os.environ:
        os.environ["ML_MODELS_DIR"] = str(models_dir)

    import plc500_ml.api as api_mod
    from fastapi.testclient import TestClient

    app = getattr(api_mod, "app", None)
    if app is None and hasattr(api_mod, "create_app"):
        app = api_mod.create_app()
    assert app is not None, "plc500_ml.api must expose 'app' or 'create_app()'"

    with TestClient(app) as client:
        yield client


@pytest.fixture(scope="session")
def ml_api_live_server(repo_root: Path) -> Iterator[str]:
    """Session-scoped live ``uvicorn`` subprocess for ``plc500_ml.api`` on ephemeral port."""
    ensure_python_pkg_on_path(repo_root)
    port = find_free_port()
    py_dir = repo_root / "python"
    env = os.environ.copy()
    existing_pp = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = f"{py_dir}{os.pathsep}{existing_pp}" if existing_pp else str(py_dir)
    env["ML_MODELS_DIR"] = str(py_dir / "models")
    env["PYTHONUNBUFFERED"] = "1"

    cmd = [
        sys.executable,
        "-m",
        "uvicorn",
        "plc500_ml.api:app",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
    ]
    proc = subprocess.Popen(
        cmd,
        cwd=str(repo_root),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    base_url = f"http://127.0.0.1:{port}"
    try:
        wait_for_http(f"{base_url}/health", timeout=15.0, proc=proc)
        yield base_url
    finally:
        _kill_process_group(proc)

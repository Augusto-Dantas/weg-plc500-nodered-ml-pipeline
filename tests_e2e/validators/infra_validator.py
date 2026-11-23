"""Pure-Python JSON Schema, Docker Compose, and Makefile validators."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator

from . import ValidationResult


def make_valid_diagnosis(**overrides: Any) -> dict[str, Any]:
    """Return a baseline valid payload conforming to ``DiagnosticoAtuacaoPLC500``."""
    base: dict[str, Any] = {
        "severidade": "NORMAL",
        "diagnostico": "Operacao nominal da bancada PLC500 sem desvios.",
        "causa_provavel": "Fluxo regular de pecas e temperatura estavel.",
        "comandos_atuadores": {
            "cmd_motor": True,
            "cmd_valvula": False,
            "cmd_alarme": False,
        },
        "requer_relatorio": False,
        "resumo_executivo": "Sistema operando em regime NORMAL_AUTO com indicadores dentro da faixa esperada.",
    }
    for key, val in overrides.items():
        if key == "comandos_atuadores" and isinstance(val, dict):
            merged = dict(base["comandos_atuadores"])
            merged.update(val)
            base["comandos_atuadores"] = merged
        else:
            base[key] = val
    return base


def load_diagnostico_schema(schema_path: Path) -> dict[str, Any]:
    """Load and validate ``diagnostico_atuacao.schema.json`` as Draft 2020-12."""
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return schema


def validate_diagnostico_json(payload: dict[str, Any], schema: dict[str, Any]) -> list[str]:
    """Validate ``payload`` against ``schema`` and return sorted error messages."""
    validator = Draft202012Validator(schema)
    errs = sorted(validator.iter_errors(payload), key=lambda e: list(e.path))
    return [f"{'/'.join(str(p) for p in err.path) or '<root>'}: {err.message}" for err in errs]


def validate_compose_file(compose_path: Path) -> ValidationResult:
    """Validate ``docker-compose.yml`` services, profiles, dependencies, and env refs."""
    if not compose_path.is_file():
        return ValidationResult(ok=False, errors=[f"Missing docker-compose.yml: {compose_path}"])

    errors: list[str] = []
    try:
        data = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        return ValidationResult(ok=False, errors=[f"YAML error in docker-compose.yml: {exc}"])

    if not isinstance(data, dict) or not isinstance(data.get("services"), dict):
        return ValidationResult(ok=False, errors=["docker-compose.yml missing 'services' mapping"])

    services: dict[str, Any] = data["services"]
    for req_svc in ("postgres", "ml-api", "node-red", "plc-simulator"):
        if req_svc not in services:
            errors.append(f"docker-compose.yml missing service {req_svc!r}")

    sim_svc = services.get("plc-simulator")
    if isinstance(sim_svc, dict):
        profiles = sim_svc.get("profiles") or []
        if "sim" not in profiles:
            errors.append("plc-simulator service must declare profiles: ['sim']")

    for core_svc in ("postgres", "ml-api", "node-red"):
        svc = services.get(core_svc)
        if isinstance(svc, dict) and "sim" in (svc.get("profiles") or []):
            errors.append(f"Core service {core_svc!r} must NOT be gated by 'sim' profile")

    nr_svc = services.get("node-red")
    if isinstance(nr_svc, dict):
        deps = nr_svc.get("depends_on") or {}
        dep_names = set(deps.keys()) if isinstance(deps, dict) else set(deps)
        for req_dep in ("postgres", "ml-api"):
            if req_dep not in dep_names:
                errors.append(f"node-red service missing depends_on for {req_dep!r}")

    pg_svc = services.get("postgres")
    if isinstance(pg_svc, dict):
        vols = " ".join(str(v) for v in (pg_svc.get("volumes") or []))
        if "init.sql" not in vols or "/docker-entrypoint-initdb.d" not in vols:
            errors.append("postgres service must mount init.sql into /docker-entrypoint-initdb.d")

    for svc_name, svc_cfg in services.items():
        if isinstance(svc_cfg, dict) and svc_cfg.get("privileged") is True:
            errors.append(f"Service {svc_name!r} must not run with privileged: true")

    raw_text = compose_path.read_text(encoding="utf-8")
    env_vars = sorted(set(re.findall(r"\$\{([A-Z0-9_]+)(?::[-?][^}]*)?\}", raw_text)))

    return ValidationResult(
        ok=not errors,
        errors=errors,
        metadata={"services": sorted(services), "env_vars": env_vars},
    )


def validate_makefile(makefile_path: Path) -> ValidationResult:
    """Validate root ``Makefile`` TAB recipe indentation, ``.PHONY``, and targets."""
    if not makefile_path.is_file():
        return ValidationResult(ok=False, errors=[f"Missing Makefile: {makefile_path}"])

    lines = makefile_path.read_text(encoding="utf-8").splitlines()
    errors: list[str] = []
    targets: list[str] = []
    has_phony = False
    in_rule = False
    line_continues = False

    for lineno, line in enumerate(lines, start=1):
        stripped = line.strip()
        if line_continues:
            line_continues = line.endswith("\\")
            continue
        if not stripped or stripped.startswith("#"):
            if not stripped:
                in_rule = False
            continue
        if line.startswith("\t"):
            if not in_rule:
                errors.append(f"Makefile line {lineno}: unexpected TAB outside a target rule")
            line_continues = line.endswith("\\")
            continue
        if line.startswith(" "):
            if in_rule:
                errors.append(
                    f"Makefile line {lineno}: recipe line indented with spaces instead of TAB"
                )
            continue

        # Variable assignment vs target
        if re.match(r"^[A-Za-z_][A-Za-z0-9_-]*\s*[:+?]?=", line):
            in_rule = False
            line_continues = line.endswith("\\")
            continue

        if ":" in line:
            lhs = line.split(":", 1)[0].strip()
            if lhs == ".PHONY":
                has_phony = True
                in_rule = False
            elif lhs and not lhs.startswith("."):
                for tgt in lhs.split():
                    if tgt not in targets:
                        targets.append(tgt)
                in_rule = True
            line_continues = line.endswith("\\")
        else:
            in_rule = False

    if not has_phony:
        errors.append("Makefile missing .PHONY declaration")
    if not targets:
        errors.append("Makefile defines no targets")

    return ValidationResult(
        ok=not errors,
        errors=errors,
        metadata={"targets": targets, "has_phony": has_phony},
    )

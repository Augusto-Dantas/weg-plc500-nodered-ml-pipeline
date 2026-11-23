"""Pure-Python validators for the opaque-box E2E test suite."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ValidationResult:
    """Uniform validation outcome returned by pure-Python E2E validators."""

    ok: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


from .diagrams import validate_pdf_diagram, validate_png_diagram, validate_svg_diagram
from .infra_validator import (
    load_diagnostico_schema,
    make_valid_diagnosis,
    validate_compose_file,
    validate_diagnostico_json,
    validate_makefile,
)
from .kicad_sexpr import SExprParseError, parse_sexpr, validate_kicad8_sch
from .nodered_js_validator import (
    assert_nodered_flows_contract,
    validate_js_syntax,
    validate_nodered_flows,
)
from .ot_contract import (
    parse_st_declarations,
    strip_st_comments,
    validate_debounce_counter,
    validate_safety_interlock,
    validate_st_csv_contract,
    validate_watchdog_order,
)
from .plcopen_xml import validate_plcopen_xml
from .repo_hygiene import (
    check_env_example_placeholders,
    check_forbidden_tracked_paths,
    git_check_ignore,
    git_tracked_files,
    scan_text_for_secrets,
    verify_overleaf_zip_parity,
)
from .sql_validator import (
    assert_plc500_sql_contract,
    validate_postgres_ddl,
)

__all__ = [
    "SExprParseError",
    "ValidationResult",
    "assert_nodered_flows_contract",
    "assert_plc500_sql_contract",
    "check_env_example_placeholders",
    "check_forbidden_tracked_paths",
    "git_check_ignore",
    "git_tracked_files",
    "load_diagnostico_schema",
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
]

"""Self-tests for the E2E test infrastructure, lazy-import AST guard, and 8 pure-Python validators."""

from __future__ import annotations

import ast
import socket
from pathlib import Path

import pytest

from tests_e2e.helpers import (
    check_forbidden_tracked_paths,
    decode_do_mask,
    delta_mod16,
    find_free_port,
    make_valid_diagnosis,
    parse_sexpr,
    scan_text_for_secrets,
    validate_compose_file,
    validate_debounce_counter,
    validate_diagnostico_json,
    validate_js_syntax,
    validate_kicad8_sch,
    validate_makefile,
    validate_plcopen_xml,
    validate_png_diagram,
    validate_postgres_ddl,
    validate_safety_interlock,
    validate_st_csv_contract,
    validate_svg_diagram,
    validate_watchdog_order,
)
from tests_e2e.validators import SExprParseError

pytestmark = pytest.mark.self_test

_FORBIDDEN_TOP_LEVEL_MODULES = {
    "pymodbus",
    "fastapi",
    "httpx",
    "uvicorn",
    "pandas",
    "numpy",
    "sklearn",
    "joblib",
    "pydantic",
    "plc500_ml",
}


def _root_pkg(name: str | None) -> str:
    return (name or "").split(".", 1)[0]


def test_self_01_ast_no_top_level_m3_imports_and_no_importorskip() -> None:
    """Enforce zero top-level M3 imports and zero ``pytest.importorskip`` calls across ``tests_e2e/``."""
    e2e_root = Path(__file__).resolve().parent
    py_files = sorted(e2e_root.rglob("*.py"))
    assert len(py_files) >= 10

    violations: list[str] = []
    for py_file in py_files:
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        rel = py_file.relative_to(e2e_root)

        # Top-level statements only (plus module-level If/Try/With blocks)
        def _walk_module_scope(stmts: list[ast.stmt], rel_path: Path = rel) -> None:
            for node in stmts:
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if _root_pkg(alias.name) in _FORBIDDEN_TOP_LEVEL_MODULES:
                            violations.append(
                                f"{rel_path}:{node.lineno} top-level import {alias.name}"
                            )
                elif isinstance(node, ast.ImportFrom):
                    if _root_pkg(node.module) in _FORBIDDEN_TOP_LEVEL_MODULES:
                        violations.append(
                            f"{rel_path}:{node.lineno} top-level from {node.module} import ..."
                        )
                elif isinstance(node, ast.If):
                    _walk_module_scope(node.body, rel_path)
                    _walk_module_scope(node.orelse, rel_path)
                elif isinstance(node, ast.Try):
                    _walk_module_scope(node.body, rel_path)
                    for h in node.handlers:
                        _walk_module_scope(h.body, rel_path)
                    _walk_module_scope(node.orelse, rel_path)
                    _walk_module_scope(node.finalbody, rel_path)
                elif isinstance(node, (ast.With, ast.AsyncWith)):
                    _walk_module_scope(node.body, rel_path)

        _walk_module_scope(tree.body)

        # Whole-file scan forbidding pytest.importorskip anywhere
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr == "importorskip":
                violations.append(f"{rel}:{node.lineno} forbidden pytest.importorskip")
            elif isinstance(node, ast.Name) and node.id == "importorskip":
                violations.append(f"{rel}:{node.lineno} forbidden importorskip reference")

    assert not violations, "Lazy-import / zero-skip AST violations:\n" + "\n".join(violations)


def test_self_02_markers_registered_in_pytest_configure(pytestconfig: pytest.Config) -> None:
    """Confirm all 11 required E2E markers are registered in ``pytest_configure``."""
    raw_markers = pytestconfig.getini("markers")
    names = {line.split(":", 1)[0].strip() for line in raw_markers}
    expected = {
        "tier1",
        "tier2",
        "tier3",
        "tier4",
        "area_a",
        "area_b",
        "area_c",
        "area_d",
        "area_e",
        "m6_final",
        "self_test",
    }
    missing = expected - names
    assert not missing, f"Unregistered pytest markers: {missing}"


def test_self_03_find_free_port_ephemeral_and_bindable() -> None:
    """Verify ``find_free_port()`` returns bindable ephemeral localhost TCP ports."""
    ports = [find_free_port() for _ in range(3)]
    for port in ports:
        assert 1024 <= port <= 65535
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", port))


def test_self_04_delta_mod16_and_decode_do_mask() -> None:
    """Verify 16-bit counter wrap math and 4-bit DO bitmask decoding."""
    assert delta_mod16(12, 0) == 12
    assert delta_mod16(0, 65535) == 1
    assert delta_mod16(5, 65530) == 11
    assert delta_mod16(0, 65524) == 12
    assert decode_do_mask(0b1001) == {"DO0": True, "DO1": False, "DO2": False, "DO3": True}
    assert decode_do_mask(0b0100) == {"DO0": False, "DO1": False, "DO2": True, "DO3": False}


def test_self_05_validator_repo_hygiene_pos_neg() -> None:
    """Self-test ``repo_hygiene`` secret and forbidden-path validators."""
    assert scan_text_for_secrets("PLC_PORT=502\nPOSTGRES_PASSWORD=CHANGE_ME_DEV\n") == []
    synthetic_pat = "ghp_" + ("a" * 26) + "012345"
    leaks = scan_text_for_secrets(f"TOKEN={synthetic_pat}\n", "leak.env")
    assert leaks

    assert check_forbidden_tracked_paths(["LICENSE", "docs/relatorio/main.tex"]).ok
    bad = check_forbidden_tracked_paths(["GEMINI.md", ".agents/teamwork/x", "node-red/flows_cred.json"])
    assert not bad.ok
    assert len(bad.errors) == 3


def test_self_06_validator_plcopen_xml_pos_neg() -> None:
    """Self-test ``plcopen_xml`` on valid TC6 v2.01 XML and negative mutations."""
    valid_xml = """<?xml version="1.0" encoding="utf-8"?>
<project xmlns="http://www.plcopen.org/xml/tc6_0201">
  <fileHeader companyName="WEG" productName="CODESYS" productVersion="3.5" creationDateTime="2026-01-01T00:00:00"/>
  <contentHeader name="plc500_project">
    <coordinateInfo><fbd><scaling x="1" y="1"/></fbd><ld><scaling x="1" y="1"/></ld><sfc><scaling x="1" y="1"/></sfc></coordinateInfo>
  </contentHeader>
  <types>
    <pous>
      <pou name="FB_DebounceCounter" pouType="functionBlock"><interface/><body><ST><xhtml xmlns="http://www.w3.org/1999/xhtml">tonDb(IN:=xIn, PT:=T#50MS);</xhtml></ST></body></pou>
      <pou name="FB_WatchdogHeartbeat" pouType="functionBlock"><interface/><body><ST><xhtml xmlns="http://www.w3.org/1999/xhtml">tonWatchdog(IN:=TRUE, PT:=T#5S);</xhtml></ST></body></pou>
      <pou name="FB_SafetyInterlock" pouType="functionBlock"><interface/><body><ST><xhtml xmlns="http://www.w3.org/1999/xhtml">uiVersao_Mapa := 100;</xhtml></ST></body></pou>
      <pou name="PLC_PRG" pouType="program"><interface/><body><ST><xhtml xmlns="http://www.w3.org/1999/xhtml">xDI_EmergenciaOK := %IX0.3;</xhtml></ST></body></pou>
    </pous>
  </types>
  <instances>
    <configurations>
      <configuration name="Config">
        <resource name="Res">
          <task name="MainTask" priority="1" interval="PT0.01S">
            <pouInstance name="PLC_PRG" typeName="PLC_PRG"/>
          </task>
          <globalVars name="GVL_Modbus">
            <variable name="xDI_Presenca"><type><BOOL/></type></variable>
            <variable name="xCmd_Motor"><type><BOOL/></type></variable>
            <variable name="uiCnt_Presenca"><type><UINT/></type></variable>
            <variable name="uiTimeout_WD_ms"><type><UINT/></type></variable>
            <variable name="uiVersao_Mapa"><type><UINT/></type></variable>
          </globalVars>
        </resource>
      </configuration>
    </configurations>
  </instances>
</project>"""
    assert validate_plcopen_xml(valid_xml).ok
    assert not validate_plcopen_xml("<project><broken").ok
    assert not validate_plcopen_xml(valid_xml.replace("tc6_0201", "tc6_0200")).ok
    assert not validate_plcopen_xml(valid_xml.replace("PT0.01S", "PT0.05S")).ok


def test_self_07_validator_ot_contract_pos_neg() -> None:
    """Self-test ``ot_contract`` ST ordering, debounce, interlock, and CSV checks."""
    wd_ok = """
    IF uiTimeout_WD_ms >= 2000 AND uiTimeout_WD_ms <= 30000 THEN
        tLimit := UINT_TO_TIME(uiTimeout_WD_ms);
    ELSE
        tLimit := T#5S;
    END_IF;
    tonWatchdog(IN := (uiHeartbeat = uiLastHeartbeat), PT := tLimit);
    uiLastHeartbeat := uiHeartbeat;
    uiEcho_Heartbeat := uiHeartbeat;
    xFlag_WatchdogAtivo := tonWatchdog.Q;
    """
    assert validate_watchdog_order(wd_ok).ok
    wd_swapped = wd_ok.replace(
        "tonWatchdog(IN := (uiHeartbeat = uiLastHeartbeat), PT := tLimit);\n    uiLastHeartbeat := uiHeartbeat;",
        "uiLastHeartbeat := uiHeartbeat;\n    tonWatchdog(IN := (uiHeartbeat = uiLastHeartbeat), PT := tLimit);",
    )
    assert not validate_watchdog_order(wd_swapped).ok

    deb_ok = """
    VAR
        tonDb : TON;
        rtEdge : R_TRIG;
        uiCnt : UINT;
    END_VAR
    tonDb(IN := xIn, PT := T#50MS);
    rtEdge(CLK := tonDb.Q);
    IF rtEdge.Q THEN uiCnt := uiCnt + 1; END_IF;
    """
    assert validate_debounce_counter(deb_ok).ok
    assert not validate_debounce_counter(deb_ok.replace("T#50MS", "T#10MS")).ok

    gvl_min = "VAR_GLOBAL xDI_Presenca : BOOL; END_VAR"
    assert not validate_st_csv_contract(gvl_min, "area,address,variable,type\nDI,0,xDI_Presenca,BOOL\n").ok

    si_min = "IF NOT xDI_EmergenciaOK THEN xFlag_EmergTravada := TRUE; ELSIF xCmd_ResetFalha AND xDI_EmergenciaOK AND NOT xDI_Sobretemp THEN xFlag_EmergTravada := FALSE; END_IF; uiVersao_Mapa := 100;"
    assert not validate_safety_interlock(si_min, "xDI_EmergenciaOK := %IX0.3;").ok


def test_self_08_validator_kicad_sexpr_pos_neg() -> None:
    """Self-test ``kicad_sexpr`` parser and KiCad 8 schematic validator."""
    parsed = parse_sexpr('(kicad_sch (property "Value" "2.2k (0.5W \\"metal\\")"))')
    assert parsed[0] == "kicad_sch"

    with pytest.raises(SExprParseError):
        parse_sexpr('(kicad_sch (unclosed "str)')
    with pytest.raises(SExprParseError):
        parse_sexpr("(kicad_sch ))")

    valid_sch = """(kicad_sch (version 20231120) (generator "eeschema") (uuid "u1")
      (lib_symbols (symbol "Device:R" (pin_numbers hide)))
      (symbol (lib_id "Device:R") (at 10 10 0))
      (symbol (lib_id "Device:R") (at 20 10 0))
      (symbol (lib_id "Device:R") (at 30 10 0))
      (symbol (lib_id "Device:R") (at 40 10 0))
      (symbol (lib_id "Device:R") (at 50 10 0))
      (wire (pts (xy 0 0) (xy 10 0)))
      (wire (pts (xy 10 0) (xy 20 0)))
      (wire (pts (xy 20 0) (xy 30 0)))
      (wire (pts (xy 30 0) (xy 40 0)))
      (wire (pts (xy 40 0) (xy 50 0)))
      (label "DI0" (at 0 0 0))
      (label "DI1" (at 10 0 0))
      (label "DO0" (at 20 0 0))
      (label "24V" (at 30 0 0))
      (sheet_instances (path "/" (page "1")))
    )"""
    assert validate_kicad8_sch(valid_sch).ok
    assert not validate_kicad8_sch(valid_sch.replace("20231120", "20230121")).ok
    assert not validate_kicad8_sch(valid_sch.replace('(lib_id "Device:R") (at 50 10 0)', '(lib_id "Missing:Ghost") (at 50 10 0)')).ok


def test_self_09_validator_diagrams_pos_neg(tmp_path: Path) -> None:
    """Self-test ``diagrams`` PNG and SVG validators with positive and blank/empty negatives."""
    from PIL import Image, ImageDraw

    good_png = tmp_path / "good.png"
    img = Image.new("RGB", (1200, 800), (255, 255, 255))
    draw = ImageDraw.Draw(img)
    draw.rectangle([50, 50, 1100, 700], outline=(0, 0, 0), width=6)
    draw.line([50, 50, 1100, 700], fill=(220, 0, 0), width=5)
    img.save(good_png, dpi=(300, 300))
    assert validate_png_diagram(good_png).ok

    blank_png = tmp_path / "blank.png"
    Image.new("RGB", (1200, 800), (255, 255, 255)).save(blank_png, dpi=(300, 300))
    assert not validate_png_diagram(blank_png).ok

    good_svg = tmp_path / "good.svg"
    good_svg.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'
        "<rect x='1' y='1' width='10' height='10'/><line x1='0' y1='0' x2='10' y2='10'/>"
        "<circle cx='50' cy='50' r='5'/><path d='M0 0 L10 10'/><text x='5' y='5'>A</text></svg>",
        encoding="utf-8",
    )
    assert validate_svg_diagram(good_svg).ok

    empty_svg = tmp_path / "empty.svg"
    empty_svg.write_text('<svg xmlns="http://www.w3.org/2000/svg"></svg>', encoding="utf-8")
    assert not validate_svg_diagram(empty_svg).ok


def test_self_10_validator_sql_pos_neg() -> None:
    """Self-test ``sql_validator`` positive DDL parsing and lexical-error rejection."""
    good_sql = """
    CREATE TABLE telemetria_sensores (
      id BIGSERIAL PRIMARY KEY,
      capturado_em TIMESTAMPTZ NOT NULL,
      cnt_emergencia INTEGER NOT NULL
    );
    CREATE INDEX idx_tel_capturado ON telemetria_sensores (capturado_em DESC);
    CREATE TABLE janelas_analiticas (
      id BIGSERIAL PRIMARY KEY,
      severidade_estatistica SMALLINT NOT NULL CHECK (severidade_estatistica BETWEEN 0 AND 3)
    );
    CREATE TABLE diagnosticos_ia (
      id BIGSERIAL PRIMARY KEY,
      janela_id BIGINT REFERENCES janelas_analiticas(id),
      origem_decisao VARCHAR(32) CHECK (origem_decisao IN ('LLM', 'FALLBACK_STATS'))
    );
    """
    res = validate_postgres_ddl(good_sql)
    assert not res.errors
    assert {"telemetria_sensores", "janelas_analiticas", "diagnosticos_ia"} <= set(res.tables)
    assert "idx_tel_capturado" in res.indexes

    bad_sql = "CREATE TABLE broken (id INT, note TEXT DEFAULT 'unterminated);"
    bad_res = validate_postgres_ddl(bad_sql)
    assert bad_res.errors


def test_self_11_validator_nodered_js_pos_neg() -> None:
    """Self-test ``nodered_js_validator`` state-machine JS lexer (templates, regex vs div, errors)."""
    good_js = r"""
    const rate = (delta / 60.0) * 60;
    const re = /foo\/[a/b]+/g;
    const msgTxt = `sev=${sevs.map(s => `${s}`).join(",")}`;
    if (re.test(msgTxt)) { return { payload: rate }; }
    return null;
    """
    assert validate_js_syntax(good_js, "good") == []
    assert validate_js_syntax("const x = { a: 1;", "unclosed_brace")
    assert validate_js_syntax("const s = `hello ${a;", "unclosed_tpl")
    assert validate_js_syntax("// TODO: finish this\nreturn msg;", "todo_banned")


def test_self_12_validator_infra_schema_compose_makefile_pos_neg(tmp_path: Path) -> None:
    """Self-test ``infra_validator`` diagnosis helper, Compose profile check, and Makefile TAB check."""
    diag = make_valid_diagnosis()
    minimal_schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "required": ["severidade", "comandos_atuadores"],
        "properties": {
            "severidade": {"type": "string", "enum": ["NORMAL", "ATENCAO", "CRITICO", "EMERGENCIA"]},
            "comandos_atuadores": {"type": "object"},
        },
    }
    assert validate_diagnostico_json(diag, minimal_schema) == []
    assert validate_diagnostico_json(make_valid_diagnosis(severidade="BAD"), minimal_schema)

    good_mk = tmp_path / "Makefile"
    good_mk.write_text(".PHONY: test\ntest:\n\t@echo ok\n", encoding="utf-8")
    assert validate_makefile(good_mk).ok

    bad_mk = tmp_path / "Makefile.bad"
    bad_mk.write_text(".PHONY: test\ntest:\n    @echo spaces\n", encoding="utf-8")
    assert not validate_makefile(bad_mk).ok

    bad_compose = tmp_path / "docker-compose.yml"
    bad_compose.write_text("services:\n  postgres:\n    image: postgres:16\n", encoding="utf-8")
    assert not validate_compose_file(bad_compose).ok

"""Tier 2 Boundary & Corner-Case E2E Tests — Area A, Area B, and Area E."""

from __future__ import annotations

import csv
import re
from pathlib import Path

import pytest
from PIL import Image

from tests_e2e.helpers import (
    SExprParseError,
    check_forbidden_tracked_paths,
    git_check_ignore,
    git_tracked_files,
    parse_sexpr,
    scan_text_for_secrets,
    validate_debounce_counter,
    validate_kicad8_sch,
    validate_pdf_diagram,
    validate_plcopen_xml,
    validate_png_diagram,
    validate_safety_interlock,
    validate_st_csv_contract,
    validate_svg_diagram,
    validate_watchdog_order,
    verify_overleaf_zip_parity,
)

pytestmark = pytest.mark.tier2


# ==============================================================================
# Area A — Tier 2 Boundary & Negative Hygiene Cases (6 tests)
# ==============================================================================


FINAL_REPORT_PDF_NAME = (
    "Arquitetura e Especificação Técnica de uma Pipeline Híbrida de Análise de Dados "
    "Industriais Integrando WEG PLC500, Comunicação Modbus TCP-IP e Orquestração com "
    "Node-RED.pdf"
)
OVERLEAF_ZIP_NAME = "overleaf_relatorio_abnt_plc500_nodered.zip"


@pytest.mark.area_a
def test_a_t2_01_overleaf_zip_content_byte_parity_with_tex_sources(
    docs_relatorio_dir: Path,
) -> None:
    """A-T2-01: Verify SHA-256 byte-for-byte parity between Overleaf ZIP and disk sources."""
    zip_path = docs_relatorio_dir / OVERLEAF_ZIP_NAME
    res = verify_overleaf_zip_parity(zip_path, docs_relatorio_dir)
    assert res.ok, f"Overleaf ZIP parity errors: {res.errors}"
    assert res.metadata["matched_count"] == 18


@pytest.mark.area_a
def test_a_t2_02_no_secret_tokens_in_any_git_tracked_file(repo_root: Path) -> None:
    """A-T2-02: Scan every Git-tracked text file for private keys or live API tokens."""
    tracked = git_tracked_files(repo_root)
    assert tracked
    findings: list[str] = []
    for rel in tracked:
        fpath = repo_root / rel
        if not fpath.is_file():
            continue
        raw = fpath.read_bytes()
        if b"\x00" in raw[:4096]:
            continue
        text = raw.decode("utf-8", errors="ignore")
        findings.extend(scan_text_for_secrets(text, rel))
    assert not findings, f"Secrets found in tracked files: {findings}"


@pytest.mark.area_a
def test_a_t2_03_gitignore_negations_preserve_examples_and_keep_real_creds_ignored(
    repo_root: Path,
) -> None:
    """A-T2-03: Verify negated ``*.example`` files are unignored while real secrets are ignored."""
    candidates = [
        ".env.example",
        "node-red/flows_cred.json.example",
        ".env",
        ".env.local",
        "prod.env",
        "node-red/flows_cred.json",
        "my_credentials.json",
        "subdir/.agents/foo",
    ]
    res = git_check_ignore(repo_root, candidates)
    assert res[".env.example"] is False
    assert res["node-red/flows_cred.json.example"] is False
    for ignored_path in candidates[2:]:
        assert res[ignored_path] is True, f"Expected {ignored_path} to be ignored"


@pytest.mark.area_a
def test_a_t2_04_docs_relatorio_no_broken_inputs_and_intact_chapter5(
    docs_relatorio_dir: Path,
) -> None:
    """A-T2-04: Verify all ``\\input``/``\\include``/``\\bibliography`` targets resolve cleanly."""
    main_tex = (docs_relatorio_dir / "main.tex").read_text(encoding="utf-8")
    for target in re.findall(r"\\(?:input|include)\{([^}]+)\}", main_tex):
        rel = target if target.endswith(".tex") else f"{target}.tex"
        assert (docs_relatorio_dir / rel).is_file(), f"Missing LaTeX target {rel}"

    for bib in re.findall(r"\\bibliography\{([^}]+)\}", main_tex):
        rel = bib if bib.endswith(".bib") else f"{bib}.bib"
        assert (docs_relatorio_dir / rel).is_file(), f"Missing bibliography {rel}"

    assert (docs_relatorio_dir / "capitulos" / "05_implementacao_nodered.tex").is_file()


@pytest.mark.area_a
def test_a_t2_05_repo_hygiene_detector_catches_synthetic_leaks() -> None:
    """A-T2-05: Negative validator test confirming hygiene scanners catch synthetic leaks."""
    synthetic_pat = "ghp_" + ("a" * 26) + "012345"
    leaks = scan_text_for_secrets(f"API={synthetic_pat}\n", "synthetic.txt")
    assert leaks
    bad_paths = check_forbidden_tracked_paths(["GEMINI.md", ".env", ".agents/state/x"])
    assert not bad_paths.ok
    assert len(bad_paths.errors) == 3


@pytest.mark.area_a
def test_a_t2_06_latex_makefile_targets_and_clean_ignores(
    repo_root: Path, docs_relatorio_dir: Path
) -> None:
    """A-T2-06: Verify ``.gitignore`` covers LaTeX clean artifacts without ignoring final PDFs."""
    mk_text = (docs_relatorio_dir / "Makefile").read_text(encoding="utf-8")
    assert "clean:" in mk_text
    exts = sorted(set(re.findall(r"\$\(MAIN\)\.([a-z0-9_.]+)", mk_text)))
    exts = [e for e in exts if e not in ("tex", "pdf")]
    assert exts

    report_pdf_rel = f"docs/relatorio/{FINAL_REPORT_PDF_NAME}"
    probes = [f"docs/relatorio/main.{ext}" for ext in exts]
    probes.extend([report_pdf_rel, "hardware/diagrams/electrical_wiring.pdf"])
    ignored = git_check_ignore(repo_root, probes)
    for ext in exts:
        assert ignored[f"docs/relatorio/main.{ext}"] is True, f"Intermediate .{ext} not ignored"
    assert ignored[report_pdf_rel] is False
    assert ignored["hardware/diagrams/electrical_wiring.pdf"] is False


# ==============================================================================
# Area B — Tier 2 OT Boundary & Mutated Negative Cases (8 tests)
# ==============================================================================


@pytest.mark.area_b
def test_b_t2_01_watchdog_evaluation_order_ton_before_last_heartbeat_update(
    plc_dir: Path,
) -> None:
    """B-T2-01: Verify ``tonWatchdog`` precedes ``uiLastHeartbeat :=`` and swapped fails."""
    wd_text = (plc_dir / "src" / "FB_WatchdogHeartbeat.st").read_text(encoding="utf-8")
    assert validate_watchdog_order(wd_text).ok

    ton_m = re.search(r"[^\n;]*\btonWatchdog\s*\([^;]*;", wd_text, re.IGNORECASE | re.DOTALL)
    upd_m = re.search(r"[^\n;]*\buiLastHeartbeat\s*:=[^;]*;", wd_text, re.IGNORECASE)
    assert ton_m is not None and upd_m is not None
    assert ton_m.start() < upd_m.start()

    # Mutate by swapping the two statements
    swapped = (
        wd_text[: ton_m.start()]
        + upd_m.group(0)
        + "\n"
        + ton_m.group(0)
        + wd_text[upd_m.end() :]
    )
    assert not validate_watchdog_order(swapped).ok


@pytest.mark.area_b
def test_b_t2_02_watchdog_timeout_boundary_expressions_and_fallback(plc_dir: Path) -> None:
    """B-T2-02: Verify inclusive ``[2000..30000]`` watchdog bounds and ``T#5S`` fallback."""
    wd_text = (plc_dir / "src" / "FB_WatchdogHeartbeat.st").read_text(encoding="utf-8")
    assert validate_watchdog_order(wd_text).ok
    # Mutating lower bound to > 2000 (off-by-one) must be rejected
    off_by_one = re.sub(r">=\s*2000", "> 2000", wd_text)
    assert not validate_watchdog_order(off_by_one).ok


@pytest.mark.area_b
def test_b_t2_03_reset_guard_requires_di3_high_and_di2_low(plc_dir: Path) -> None:
    """B-T2-03: Verify emergency reset requires ``xDI_EmergenciaOK`` AND ``NOT xDI_Sobretemp``."""
    si_text = (plc_dir / "src" / "FB_SafetyInterlock.st").read_text(encoding="utf-8")
    prg_text = (plc_dir / "src" / "PLC_PRG.st").read_text(encoding="utf-8")
    assert validate_safety_interlock(si_text, prg_text).ok

    broken_reset = re.sub(r"NOT\s*\(?\s*xDI_Sobretemp\s*\)?", "TRUE", si_text, flags=re.IGNORECASE)
    assert not validate_safety_interlock(broken_reset, prg_text).ok


@pytest.mark.area_b
def test_b_t2_04_versao_mapa_100_enforced_unconditionally_every_scan(plc_dir: Path) -> None:
    """B-T2-04: Verify ``uiVersao_Mapa := 100`` executes unconditionally every scan."""
    si_text = (plc_dir / "src" / "FB_SafetyInterlock.st").read_text(encoding="utf-8")
    prg_text = (plc_dir / "src" / "PLC_PRG.st").read_text(encoding="utf-8")
    assert validate_safety_interlock(si_text, prg_text).ok

    # Negative check: trapping uiVersao_Mapa := 100 inside IF FALSE THEN must fail
    trapped_si = re.sub(
        r"uiVersao_Mapa\s*:=\s*100\s*;",
        "IF FALSE THEN uiVersao_Mapa := 100; END_IF;",
        si_text,
        flags=re.IGNORECASE,
    )
    trapped_prg = re.sub(
        r"uiVersao_Mapa\s*:=\s*100\s*;",
        "IF FALSE THEN uiVersao_Mapa := 100; END_IF;",
        prg_text,
        flags=re.IGNORECASE,
    )
    assert not validate_safety_interlock(trapped_si, trapped_prg).ok


@pytest.mark.area_b
def test_b_t2_05_uint16_wraparound_and_debounce_50ms_boundary(plc_dir: Path) -> None:
    """B-T2-05: Verify ``FB_DebounceCounter`` uses ``T#50MS`` and unclamped ``UINT`` wrap-16."""
    deb_text = (plc_dir / "src" / "FB_DebounceCounter.st").read_text(encoding="utf-8")
    assert validate_debounce_counter(deb_text).ok

    clamped = deb_text + "\nIF uiContagem < 65535 THEN uiContagem := uiContagem + 1; END_IF;\n"
    assert not validate_debounce_counter(clamped).ok


@pytest.mark.area_b
def test_b_t2_06_plcopen_xml_validator_rejects_malformed_and_missing_tc6_schema(
    plc_dir: Path,
) -> None:
    """B-T2-06: Negative tests confirming ``validate_plcopen_xml`` catches 5 XML defects."""
    xml_text = (plc_dir / "codesys" / "plc500_project_plcopen.xml").read_text(encoding="utf-8")
    assert validate_plcopen_xml(xml_text).ok

    mutations = (
        xml_text[: len(xml_text) // 2],  # malformed / unclosed XML
        xml_text.replace("tc6_0201", "tc6_0200"),  # wrong TC6 namespace
        re.sub(r"<fileHeader[^>]*/>|<fileHeader.*?</fileHeader>", "", xml_text, flags=re.DOTALL),
        xml_text.replace("FB_SafetyInterlock", "FB_RenamedInterlock"),
        re.sub(r'interval="[^"]+"', 'interval="PT0.05S"', xml_text),
    )
    for idx, mutated in enumerate(mutations):
        res = validate_plcopen_xml(mutated)
        assert not res.ok, f"Mutation #{idx} unexpectedly passed PLCopen XML validation"


@pytest.mark.area_b
def test_b_t2_07_st_csv_validator_rejects_1based_address_or_missing_register(
    plc_dir: Path,
) -> None:
    """B-T2-07: Negative tests confirming ``validate_st_csv_contract`` catches CSV/GVL drift."""
    gvl_text = (plc_dir / "src" / "GVL_Modbus.st").read_text(encoding="utf-8")
    csv_path = plc_dir / "codesys" / "modbus_mapping.csv"
    csv_text = csv_path.read_text(encoding="utf-8")
    assert validate_st_csv_contract(gvl_text, csv_text).ok

    # (a) Missing IR[3] uiCnt_Emergencia
    no_emerg = "\n".join(
        line for line in csv_text.splitlines() if "uiCnt_Emergencia" not in line
    )
    assert not validate_st_csv_contract(gvl_text, no_emerg).ok

    # (b) Type mismatch in GVL_Modbus
    bad_gvl = gvl_text.replace("uiEstado_CLP : UINT", "uiEstado_CLP : BOOL")
    assert not validate_st_csv_contract(bad_gvl, csv_text).ok

    # (c) Unknown variable not in GVL
    unknown_var = csv_text.replace("xCmd_Motor", "xCmd_Phantom")
    assert not validate_st_csv_contract(gvl_text, unknown_var).ok

    # (d) Out-of-range 1-based Coil[5]
    one_based = csv_text + "\nCoil,5,xCmd_Motor,BOOL,FC05\n"
    assert not validate_st_csv_contract(gvl_text, one_based).ok


@pytest.mark.area_b
def test_b_t2_08_simultaneous_fault_precedence_sobretemp_and_watchdog(plc_dir: Path) -> None:
    """B-T2-08: Verify strict interlock precedence and reject swapped Sobretemp/Watchdog order."""
    si_text = (plc_dir / "src" / "FB_SafetyInterlock.st").read_text(encoding="utf-8")
    prg_text = (plc_dir / "src" / "PLC_PRG.st").read_text(encoding="utf-8")
    assert validate_safety_interlock(si_text, prg_text).ok

    # Swapping ELSIF xDI_Sobretemp and ELSIF xFlag_WatchdogAtivo must fail
    swapped_order = si_text.replace("xDI_Sobretemp", "__TMP_WD__").replace(
        "xFlag_WatchdogAtivo", "xDI_Sobretemp", 1
    ).replace("__TMP_WD__", "xFlag_WatchdogAtivo", 1)
    assert not validate_safety_interlock(swapped_order, prg_text).ok


# ==============================================================================
# Area E — Tier 2 Hardware, Diagram & KiCad Boundary Cases (6 tests)
# ==============================================================================


@pytest.mark.area_e
def test_e_t2_01_kicad_sexpr_parser_handles_escaped_quotes_and_rejects_unbalanced() -> None:
    """E-T2-01: Verify S-expression parser handles escaped quotes and fails with line/col info."""
    expr = parse_sexpr('(kicad_sch (property "Value" "2.2k (0.5W \\"metal\\")"))')
    assert expr[1][2] == '2.2k (0.5W "metal")'

    for bad in ('(kicad_sch (a 1)', '(kicad_sch ))', '', '(kicad_sch "unclosed)'):
        with pytest.raises(SExprParseError) as exc_info:
            parse_sexpr(bad)
        assert exc_info.value.line >= 1 and exc_info.value.col >= 1


@pytest.mark.area_e
def test_e_t2_02_kicad8_validator_version_boundary_and_dangling_lib_id(
    hardware_dir: Path,
) -> None:
    """E-T2-02: Verify KiCad 8 schematic rejects old versions, missing sheets, and dangling libs."""
    sch_text = (hardware_dir / "kicad" / "plc500_bench.kicad_sch").read_text(encoding="utf-8")
    assert validate_kicad8_sch(sch_text).ok

    assert not validate_kicad8_sch(sch_text.replace("20231120", "20211123")).ok
    assert not validate_kicad8_sch(sch_text.replace("20231120", "20230121")).ok
    assert not validate_kicad8_sch(sch_text.replace("(sheet_instances", "(removed_sheets")).ok

    dangling = re.sub(
        r'\(lib_id\s+"[^"]+"\)',
        '(lib_id "MissingLib:Ghost")',
        sch_text,
        count=1,
    )
    assert not validate_kicad8_sch(dangling).ok


@pytest.mark.area_e
def test_e_t2_03_diagram_png_non_blank_pixel_variance_and_300dpi_check(
    hardware_dir: Path, tmp_path: Path
) -> None:
    """E-T2-03: Verify real 300 DPI PNGs have non-blank pixel variance and blank PNGs fail."""
    diag_dir = hardware_dir / "diagrams"
    for stem in ("electrical_wiring", "network_and_bench_layout"):
        res = validate_png_diagram(
            diag_dir / f"{stem}.png",
            matching_pdf=diag_dir / f"{stem}.pdf",
            min_width=1200,
            min_height=600,
        )
        assert res.ok, f"{stem}.png failed boundary check: {res.errors}"
        assert res.metadata["max_rgb_stddev"] > 5.0
        assert 0.005 < res.metadata["ink_fraction"] < 0.95

    blank = tmp_path / "blank_white.png"
    Image.new("RGB", (1400, 900), (255, 255, 255)).save(blank, dpi=(300, 300))
    assert not validate_png_diagram(blank).ok

    solid = tmp_path / "solid_black.png"
    Image.new("RGB", (1400, 900), (0, 0, 0)).save(solid, dpi=(300, 300))
    assert not validate_png_diagram(solid).ok


@pytest.mark.area_e
def test_e_t2_04_diagram_svg_viewbox_bounds_and_pdf_render_parity(
    hardware_dir: Path, tmp_path: Path
) -> None:
    """E-T2-04: Verify SVG viewBox/primitives and PDF render parity, rejecting empty SVGs/PDFs."""
    diag_dir = hardware_dir / "diagrams"
    for stem in ("electrical_wiring", "network_and_bench_layout"):
        assert validate_svg_diagram(diag_dir / f"{stem}.svg", min_elements=5).ok
        assert validate_pdf_diagram(diag_dir / f"{stem}.pdf", min_pages=1).ok

    empty_svg = tmp_path / "empty.svg"
    empty_svg.write_text('<svg xmlns="http://www.w3.org/2000/svg"></svg>', encoding="utf-8")
    assert not validate_svg_diagram(empty_svg).ok

    corrupt_pdf = tmp_path / "bad.pdf"
    corrupt_pdf.write_bytes(b"%PDF-1.7\ncorrupted-bytes")
    assert not validate_pdf_diagram(corrupt_pdf).ok


@pytest.mark.area_e
def test_e_t2_05_bom_csv_column_uniformity_and_numeric_quantities(hardware_dir: Path) -> None:
    """E-T2-05: Verify ``hardware/bom.csv`` uniform columns, positive quantities, and >= 4 res."""
    bom_path = hardware_dir / "bom.csv"
    with bom_path.open("r", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        assert reader.fieldnames
        qty_col = next(
            (c for c in reader.fieldnames if any(k in c.lower() for k in ("qty", "quant", "qtd"))),
            None,
        )
        assert qty_col is not None, f"No quantity column found in {reader.fieldnames}"
        resistor_qty = 0.0
        for idx, row in enumerate(reader, start=2):
            assert None not in row, f"Ragged extra columns at bom.csv row {idx}: {row}"
            assert all(v is not None for v in row.values()), f"Missing column at row {idx}: {row}"
            qty_val = float(str(row[qty_col]).strip())
            assert qty_val > 0, f"Non-positive BOM quantity at row {idx}: {qty_val}"
            joined = " ".join(str(v) for v in row.values()).lower()
            if "2.2" in joined and ("res" in joined or "ohm" in joined or "k" in joined):
                resistor_qty += qty_val
        assert resistor_qty >= 4, f"Expected >= 4 2.2k resistors in BOM, got {resistor_qty}"


@pytest.mark.area_e
def test_e_t2_06_cross_artifact_electrical_and_network_consistency(hardware_dir: Path) -> None:
    """E-T2-06: Verify all 5 hardware artifacts agree on IPs, 24 Vcc, 2.2k, and DI/DO channels."""
    artifacts = (
        hardware_dir / "bom.csv",
        hardware_dir / "wiring_table.md",
        hardware_dir / "latex" / "schematic_electrical.tex",
        hardware_dir / "latex" / "schematic_network_bench.tex",
        hardware_dir / "kicad" / "plc500_bench.kicad_sch",
    )
    texts = {p.name: p.read_text(encoding="utf-8") for p in artifacts}

    for name in ("wiring_table.md", "schematic_network_bench.tex"):
        assert "192.168.20.10" in texts[name], f"{name} missing ETH1 IP"
        assert "192.168.10.10" in texts[name], f"{name} missing ETH2 IP"
        assert "192.168.10.20" in texts[name], f"{name} missing IT host IP"
        assert "502" in texts[name], f"{name} missing port 502"

    for name in ("bom.csv", "wiring_table.md", "schematic_electrical.tex", "plc500_bench.kicad_sch"):
        assert "24" in texts[name], f"{name} missing 24V reference"
        assert "2.2" in texts[name], f"{name} missing 2.2k resistor spec"

    for name in ("wiring_table.md", "schematic_electrical.tex", "plc500_bench.kicad_sch"):
        for ch in ("DI0", "DI1", "DI2", "DI3", "DO0", "DO1", "DO2", "DO3"):
            assert ch in texts[name], f"{name} missing channel {ch}"

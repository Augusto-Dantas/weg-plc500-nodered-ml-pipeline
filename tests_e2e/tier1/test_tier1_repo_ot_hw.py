"""Tier 1 Feature Coverage E2E Tests — Area A (Repo/LaTeX), Area B (OT/PLC), Area E (HW/KiCad)."""

from __future__ import annotations

import ast
import csv
import json
import re
import shutil
import subprocess
import zipfile
from pathlib import Path

import pypdfium2 as pdfium
import pytest
import yaml

from tests_e2e.helpers import (
    check_env_example_placeholders,
    check_forbidden_tracked_paths,
    git_check_ignore,
    git_tracked_files,
    parse_sexpr,
    parse_st_declarations,
    scan_text_for_secrets,
    strip_st_comments,
    validate_debounce_counter,
    validate_kicad8_sch,
    validate_pdf_diagram,
    validate_plcopen_xml,
    validate_png_diagram,
    validate_safety_interlock,
    validate_st_csv_contract,
    validate_svg_diagram,
    validate_watchdog_order,
)
from tests_e2e.validators.ot_contract import CANONICAL_MODBUS_MAP

pytestmark = pytest.mark.tier1


# ==============================================================================
# Area A — Monorepo Migration, Git Hygiene, Toolchain, Docs & CI
# ==============================================================================


@pytest.mark.area_a
def test_a_t1_01_latex_report_migration_complete(
    repo_root: Path, docs_relatorio_dir: Path
) -> None:
    """A-T1-01: Verify complete ABNT LaTeX report migration under ``docs/relatorio/``."""
    expected_rel = [
        "main.tex",
        "main.pdf",
        "Makefile",
        ".latexmkrc",
        "README.md",
        "referencias.bib",
        "overleaf_relatorio_abnt_plc500_n8n.zip",
        "config/metadados.tex",
        "config/pacotes.tex",
        "pretextual/abstract.tex",
        "pretextual/folha_rosto_capa.tex",
        "pretextual/resumo.tex",
        "pretextual/siglas.tex",
        "pretextual/simbolos.tex",
        "capitulos/01_introducao.tex",
        "capitulos/02_fundamentacao.tex",
        "capitulos/03_arquitetura.tex",
        "capitulos/04_implementacao_clp.tex",
        "capitulos/05_implementacao_nodered.tex",
        "capitulos/06_plano_validacao.tex",
        "capitulos/07_consideracoes_finais.tex",
        "apendices/apendice_a_checklist.tex",
    ]
    missing = [rel for rel in expected_rel if not (docs_relatorio_dir / rel).is_file()]
    assert not missing, f"Missing migrated LaTeX artifacts: {missing}"

    forbidden_at_root = [
        "main.tex",
        "main.pdf",
        ".latexmkrc",
        "referencias.bib",
        "overleaf_relatorio_abnt_plc500_n8n.zip",
        "config",
        "pretextual",
        "capitulos",
        "apendices",
    ]
    leftover = [name for name in forbidden_at_root if (repo_root / name).exists()]
    assert not leftover, f"Unmigrated LaTeX artifacts remain at repo root: {leftover}"

    main_pdf = docs_relatorio_dir / "main.pdf"
    assert main_pdf.read_bytes()[:5] == b"%PDF-"
    doc = pdfium.PdfDocument(str(main_pdf))
    try:
        assert len(doc) >= 40, f"Expected >= 40 pages in main.pdf, got {len(doc)}"
    finally:
        doc.close()

    zip_path = docs_relatorio_dir / "overleaf_relatorio_abnt_plc500_n8n.zip"
    with zipfile.ZipFile(zip_path, "r") as zf:
        members = [m for m in zf.namelist() if not m.endswith("/")]
        assert len(members) == 18
        for member in members:
            assert (docs_relatorio_dir / member).is_file(), f"Missing ZIP target: {member}"


@pytest.mark.area_a
def test_a_t1_02_git_identity_license_and_env_example(repo_root: Path) -> None:
    """A-T1-02: Verify Git identity, MIT ``LICENSE``, and ``.env.example`` contract."""
    name_proc = subprocess.run(
        ["git", "-C", str(repo_root), "config", "user.name"],
        check=True,
        capture_output=True,
        text=True,
    )
    email_proc = subprocess.run(
        ["git", "-C", str(repo_root), "config", "user.email"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert name_proc.stdout.strip() == "Augusto-Dantas"
    assert email_proc.stdout.strip() == "augusto.p.dantas8@gmail.com"

    lic_text = (repo_root / "LICENSE").read_text(encoding="utf-8")
    assert "MIT License" in lic_text
    assert "Permission is hereby granted, free of charge" in lic_text

    env_text = (repo_root / ".env.example").read_text(encoding="utf-8")
    required_assignments = (
        "PLC_HOST=192.168.10.10",
        "PLC_PORT=502",
        "PLC_UNIT_ID=1",
        "PLC_WATCHDOG_TIMEOUT_MS=5000",
        "PLC_MODBUS_MAP_VERSION=100",
        "ML_API_PORT=8000",
        "NR_PORT=1880",
    )
    for req in required_assignments:
        assert req in env_text, f".env.example missing {req}"
    for req_key in ("POSTGRES_DB=", "POSTGRES_USER=", "POSTGRES_PASSWORD=", "LLM_PROVIDER="):
        assert req_key in env_text, f".env.example missing {req_key}"

    placeholder_errors = check_env_example_placeholders(env_text)
    assert not placeholder_errors, f".env.example placeholder errors: {placeholder_errors}"


@pytest.mark.area_a
def test_a_t1_03_gitignore_rules_and_zero_tracked_secrets(repo_root: Path) -> None:
    """A-T1-03: Verify ``.gitignore`` rules and zero tracked secrets/envs/venvs."""
    candidates = [
        "GEMINI.md",
        ".venv/bin/python",
        ".env",
        ".env.production",
        "flows_cred.json",
        "private.key",
        "cert.pem",
        ".agents/teamwork/x",
        "docs/relatorio/main.pdf",
        "hardware/kicad/plc500_bench.kicad_sch",
    ]
    ignored = git_check_ignore(repo_root, candidates)
    for secret_path in candidates[:8]:
        assert ignored[secret_path] is True, f"{secret_path} must be ignored by .gitignore"
    assert ignored["docs/relatorio/main.pdf"] is False
    assert ignored["hardware/kicad/plc500_bench.kicad_sch"] is False

    tracked = git_tracked_files(repo_root)
    hygiene_res = check_forbidden_tracked_paths(tracked)
    assert hygiene_res.ok, f"Tracked forbidden files: {hygiene_res.errors}"


@pytest.mark.area_a
def test_a_t1_04_local_toolchain_provisioned(repo_root: Path) -> None:
    """A-T1-04: Verify ``uv``, Python 3.12 ``.venv``, and TinyTeX ``circuitikz``/``standalone``."""
    uv_bin = Path.home() / ".local" / "bin" / "uv"
    if not uv_bin.is_file():
        which_uv = shutil.which("uv")
        assert which_uv is not None, f"uv not found at {uv_bin} or PATH"
        uv_bin = Path(which_uv)
    uv_proc = subprocess.run([str(uv_bin), "--version"], check=True, capture_output=True, text=True)
    assert uv_proc.returncode == 0

    venv_py = repo_root / ".venv" / "bin" / "python"
    assert venv_py.is_file(), f"Missing .venv Python at {venv_py}"
    probe = subprocess.run(
        [
            str(venv_py),
            "-c",
            (
                "import sys, pytest, jsonschema, yaml, pypdfium2, PIL; "
                "assert sys.version_info[:2] == (3, 12)"
            ),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert probe.returncode == 0

    tinytex_bin = Path.home() / ".TinyTeX" / "bin" / "x86_64-linux"
    pdflatex = tinytex_bin / "pdflatex"
    kpsewhich = tinytex_bin / "kpsewhich"
    if not pdflatex.is_file():
        which_pdf = shutil.which("pdflatex")
        assert which_pdf is not None, "pdflatex not found in ~/.TinyTeX or PATH"
        pdflatex = Path(which_pdf)
    if not kpsewhich.is_file():
        which_kpse = shutil.which("kpsewhich")
        assert which_kpse is not None, "kpsewhich not found in ~/.TinyTeX or PATH"
        kpsewhich = Path(which_kpse)

    subprocess.run([str(pdflatex), "--version"], check=True, capture_output=True, text=True)
    kpse = subprocess.run(
        [str(kpsewhich), "circuitikz.sty", "standalone.cls"],
        check=True,
        capture_output=True,
        text=True,
    )
    paths = [line.strip() for line in kpse.stdout.splitlines() if line.strip()]
    assert len(paths) == 2
    for sty in paths:
        assert Path(sty).is_file(), f"TeX package missing on disk: {sty}"


@pytest.mark.area_a
@pytest.mark.m6_final
def test_a_t1_05_bilingual_root_readmes_present_and_complete(repo_root: Path) -> None:
    """A-T1-05 (M6): Verify bilingual root ``README.md`` (EN) and ``README.pt-BR.md`` (PT-BR)."""
    en_path = repo_root / "README.md"
    pt_path = repo_root / "README.pt-BR.md"
    assert en_path.is_file(), f"Missing {en_path}"
    assert pt_path.is_file(), f"Missing {pt_path}"

    en_text = en_path.read_text(encoding="utf-8")
    pt_text = pt_path.read_text(encoding="utf-8")
    assert len(en_text) >= 1500
    assert len(pt_text) >= 1500
    assert "README.pt-BR.md" in en_text
    assert "README.md" in pt_text

    for doc_name, txt in (("README.md", en_text), ("README.pt-BR.md", pt_text)):
        for required_ref in (
            "PLC500",
            "Modbus",
            "Node-RED",
            "PostgreSQL",
            "FastAPI",
            "docs/relatorio",
            "plc/",
            "python/",
            "data/",
            "node-red/",
            "hardware/",
            "192.168.20.10",
            "192.168.10.10",
            "502",
            "MIT",
        ):
            assert required_ref.lower() in txt.lower(), (
                f"{doc_name} missing required reference {required_ref!r}"
            )


@pytest.mark.area_a
@pytest.mark.m6_final
def test_a_t1_06_github_actions_ci_workflow_valid(repo_root: Path) -> None:
    """A-T1-06 (M6): Verify ``.github/workflows/ci.yml`` YAML structure and validation steps."""
    ci_path = repo_root / ".github" / "workflows" / "ci.yml"
    assert ci_path.is_file(), f"Missing CI workflow: {ci_path}"

    ci_raw = ci_path.read_text(encoding="utf-8")
    assert not scan_text_for_secrets(ci_raw, "ci.yml")

    ci_data = yaml.safe_load(ci_raw)
    assert isinstance(ci_data, dict)
    # PyYAML 1.1 parses unquoted `on:` as boolean True
    on_block = ci_data.get("on") if "on" in ci_data else ci_data.get(True)
    assert on_block is not None, "ci.yml missing 'on' trigger block"
    if isinstance(on_block, dict):
        assert "push" in on_block and "pull_request" in on_block
    jobs = ci_data.get("jobs")
    assert isinstance(jobs, dict) and jobs, "ci.yml missing 'jobs'"

    lower_ci = ci_raw.lower()
    for tool_kw in ("ruff", "pytest", "flows.json", "init.sql", "plcopen", "kicad"):
        assert tool_kw in lower_ci, f"ci.yml missing validation step referencing {tool_kw!r}"


# ==============================================================================
# Area B — OT Structured Text, PLCopen TC6 XML & Modbus CSV
# ==============================================================================


@pytest.mark.area_b
def test_b_t1_01_gvl_modbus_zero_based_map_and_defaults(plc_dir: Path) -> None:
    """B-T1-01: Verify ``GVL_Modbus.st`` declares all 23 0-based Modbus variables & defaults."""
    gvl_path = plc_dir / "src" / "GVL_Modbus.st"
    assert gvl_path.is_file(), f"Missing {gvl_path}"
    gvl_text = gvl_path.read_text(encoding="utf-8")
    decls = parse_st_declarations(gvl_text)

    for _area, _addr, accepted_names, exp_type in CANONICAL_MODBUS_MAP:
        matched = [n for n in accepted_names if n in decls]
        assert matched, f"GVL_Modbus.st missing variable {accepted_names}"
        vname = matched[0]
        actual_type, _init = decls[vname]
        assert actual_type == exp_type, f"{vname} expected {exp_type}, got {actual_type}"

    _, wd_init = decls["uiTimeout_WD_ms"]
    _, ver_init = decls["uiVersao_Mapa"]
    assert wd_init == "5000" or "5000" in gvl_text
    assert ver_init == "100" or "100" in gvl_text


@pytest.mark.area_b
def test_b_t1_02_fb_debounce_counter_ton50ms_and_no_debounce_di3(plc_dir: Path) -> None:
    """B-T1-02: Verify ``FB_DebounceCounter.st`` TON 50 ms + R_TRIG and direct ``%IX0.3`` NF."""
    deb_path = plc_dir / "src" / "FB_DebounceCounter.st"
    prg_path = plc_dir / "src" / "PLC_PRG.st"
    assert deb_path.is_file(), f"Missing {deb_path}"
    assert prg_path.is_file(), f"Missing {prg_path}"

    deb_res = validate_debounce_counter(deb_path.read_text(encoding="utf-8"))
    assert deb_res.ok, f"FB_DebounceCounter errors: {deb_res.errors}"

    prg_clean = strip_st_comments(prg_path.read_text(encoding="utf-8"))
    for ix in ("%IX0.0", "%IX0.1", "%IX0.2", "%IX0.3"):
        assert ix in prg_clean.upper(), f"PLC_PRG.st missing physical input {ix}"
    assert re.search(r"xDI_EmergenciaOK\s*:=\s*%IX0\.3\b", prg_clean, re.IGNORECASE)
    assert not re.search(r"(?:TON|FB_DebounceCounter)[^;]*%IX0\.3", prg_clean, re.IGNORECASE)


@pytest.mark.area_b
def test_b_t1_03_fb_watchdog_heartbeat_bounds_and_echo(plc_dir: Path) -> None:
    """B-T1-03: Verify ``FB_WatchdogHeartbeat.st`` bounds [2000..30000], fallback 5000, & echo."""
    wd_path = plc_dir / "src" / "FB_WatchdogHeartbeat.st"
    assert wd_path.is_file(), f"Missing {wd_path}"
    res = validate_watchdog_order(wd_path.read_text(encoding="utf-8"))
    assert res.ok, f"FB_WatchdogHeartbeat errors: {res.errors}"


@pytest.mark.area_b
def test_b_t1_04_fb_safety_interlock_strict_precedence_and_bitmask(plc_dir: Path) -> None:
    """B-T1-04: Verify ``FB_SafetyInterlock.st`` strict IF/ELSIF priority, reset guard & mask."""
    si_path = plc_dir / "src" / "FB_SafetyInterlock.st"
    prg_path = plc_dir / "src" / "PLC_PRG.st"
    assert si_path.is_file(), f"Missing {si_path}"
    assert prg_path.is_file(), f"Missing {prg_path}"

    si_text = si_path.read_text(encoding="utf-8")
    prg_text = prg_path.read_text(encoding="utf-8")
    res = validate_safety_interlock(si_text, prg_text)
    assert res.ok, f"Safety interlock contract errors: {res.errors}"

    combined = strip_st_comments(si_text + "\n" + prg_text)
    assert "wMascara_DO_Real" in combined
    for qx in ("%QX0.0", "%QX0.1", "%QX0.2", "%QX0.3"):
        assert qx in combined.upper(), f"Missing physical output {qx} in ST sources"


@pytest.mark.area_b
def test_b_t1_05_plcopen_tc6_xml_v201_structure_and_maintask(plc_dir: Path) -> None:
    """B-T1-05: Verify ``plc500_project_plcopen.xml`` against PLCopen TC6 v2.01 schema."""
    xml_path = plc_dir / "codesys" / "plc500_project_plcopen.xml"
    res = validate_plcopen_xml(xml_path)
    assert res.ok, f"PLCopen XML errors: {res.errors}"


@pytest.mark.area_b
def test_b_t1_06_modbus_mapping_csv_matches_st_and_contract(plc_dir: Path) -> None:
    """B-T1-06: Verify ``modbus_mapping.csv`` 23-item 0-based parity with ``GVL_Modbus.st``."""
    gvl_path = plc_dir / "src" / "GVL_Modbus.st"
    csv_path = plc_dir / "codesys" / "modbus_mapping.csv"
    assert gvl_path.is_file(), f"Missing {gvl_path}"
    res = validate_st_csv_contract(gvl_path.read_text(encoding="utf-8"), csv_path)
    assert res.ok, f"ST/CSV contract errors: {res.errors}"


@pytest.mark.area_b
def test_b_t1_07_plc_docs_cover_codesys_setup_and_network_contract(plc_dir: Path) -> None:
    """B-T1-07: Verify ``codesys_setup_guide.md`` and ``plc/README.md`` completeness."""
    guide_path = plc_dir / "docs" / "codesys_setup_guide.md"
    readme_path = plc_dir / "README.md"
    assert guide_path.is_file(), f"Missing {guide_path}"
    assert readme_path.is_file(), f"Missing {readme_path}"

    guide_txt = guide_path.read_text(encoding="utf-8")
    readme_txt = readme_path.read_text(encoding="utf-8")
    assert len(guide_txt) >= 1000
    assert len(readme_txt) >= 1000

    combined = f"{guide_txt}\n{readme_txt}"
    for token in (
        "192.168.20.10",
        "192.168.10.10",
        "502",
        "MainTask",
        "10 ms",
        "GVL_Modbus.st",
        "FB_DebounceCounter.st",
        "FB_WatchdogHeartbeat.st",
        "FB_SafetyInterlock.st",
        "PLC_PRG.st",
        "plc500_project_plcopen.xml",
        "modbus_mapping.csv",
    ):
        assert token.lower() in combined.lower(), f"plc docs missing reference {token!r}"


# ==============================================================================
# Area E — Hardware BOM, Wiring, CircuiTikZ Diagrams & KiCad 8
# ==============================================================================


@pytest.mark.area_e
def test_e_t1_01_hardware_bom_completeness_and_specs(hardware_dir: Path) -> None:
    """E-T1-01: Verify ``hardware/bom.csv`` covers PLC500, 24 Vcc PSU, B1..B4, L1..L4, 2.2k res."""
    bom_path = hardware_dir / "bom.csv"
    assert bom_path.is_file(), f"Missing {bom_path}"

    with bom_path.open("r", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) >= 8, f"Expected >= 8 BOM line items, got {len(rows)}"

    full_text = bom_path.read_text(encoding="utf-8").lower()
    for kw in ("plc500", "24", "2.2", "b4", "led"):
        assert kw in full_text, f"bom.csv missing expected hardware keyword {kw!r}"


@pytest.mark.area_e
def test_e_t1_02_wiring_table_and_hardware_readme_contract(hardware_dir: Path) -> None:
    """E-T1-02: Verify ``wiring_table.md`` and ``hardware/README.md`` electrical & network rules."""
    wt_path = hardware_dir / "wiring_table.md"
    rd_path = hardware_dir / "README.md"
    assert wt_path.is_file(), f"Missing {wt_path}"
    assert rd_path.is_file(), f"Missing {rd_path}"

    combined = f"{wt_path.read_text(encoding='utf-8')}\n{rd_path.read_text(encoding='utf-8')}"
    for token in (
        "DI0",
        "DI1",
        "DI2",
        "DI3",
        "%IX0.0",
        "%IX0.3",
        "DO0",
        "DO3",
        "%QX0.0",
        "24",
        "PNP",
        "2.2",
        "192.168.20.10",
        "192.168.10.10",
        "502",
        "192.168.10.20",
    ):
        assert token.lower() in combined.lower(), f"Hardware docs missing {token!r}"


@pytest.mark.area_e
def test_e_t1_03_circuitikz_latex_sources_and_generator_script_valid(
    hardware_dir: Path,
) -> None:
    """E-T1-03: Verify CircuiTikZ/TikZ ``.tex`` sources and ``generate_schematics.py`` AST."""
    elec_tex = hardware_dir / "latex" / "schematic_electrical.tex"
    net_tex = hardware_dir / "latex" / "schematic_network_bench.tex"
    gen_py = hardware_dir / "scripts" / "generate_schematics.py"
    for p in (elec_tex, net_tex, gen_py):
        assert p.is_file(), f"Missing {p}"

    elec_txt = elec_tex.read_text(encoding="utf-8")
    assert "standalone" in elec_txt
    assert "circuitikz" in elec_txt or "tikz" in elec_txt
    for ch in ("DI0", "DI3", "DO0", "DO3"):
        assert ch in elec_txt, f"schematic_electrical.tex missing {ch}"

    net_txt = net_tex.read_text(encoding="utf-8")
    assert "standalone" in net_txt
    assert "tikz" in net_txt
    for tok in ("ETH1", "ETH2", "192.168.10.10", "192.168.20.10", "502"):
        assert tok in net_txt, f"schematic_network_bench.tex missing {tok}"

    gen_src = gen_py.read_text(encoding="utf-8")
    ast.parse(gen_src, filename=str(gen_py))
    assert "pdflatex" in gen_src
    assert "pypdfium2" in gen_src


@pytest.mark.area_e
def test_e_t1_04_compiled_diagrams_pdf_svg_png_exist_and_readable(hardware_dir: Path) -> None:
    """E-T1-04: Verify all 6 compiled diagrams (PDF, SVG, 300 DPI PNG) are valid & non-blank."""
    diag_dir = hardware_dir / "diagrams"
    for stem in ("electrical_wiring", "network_and_bench_layout"):
        pdf_p = diag_dir / f"{stem}.pdf"
        svg_p = diag_dir / f"{stem}.svg"
        png_p = diag_dir / f"{stem}.png"

        pdf_res = validate_pdf_diagram(pdf_p)
        assert pdf_res.ok, f"{pdf_p.name} invalid: {pdf_res.errors}"

        svg_res = validate_svg_diagram(svg_p)
        assert svg_res.ok, f"{svg_p.name} invalid: {svg_res.errors}"

        png_res = validate_png_diagram(png_p, matching_pdf=pdf_p)
        assert png_res.ok, f"{png_p.name} invalid: {png_res.errors}"


@pytest.mark.area_e
def test_e_t1_05_kicad8_project_json_valid(hardware_dir: Path) -> None:
    """E-T1-05: Verify ``hardware/kicad/plc500_bench.kicad_pro`` is valid KiCad 8 JSON."""
    pro_path = hardware_dir / "kicad" / "plc500_bench.kicad_pro"
    assert pro_path.is_file(), f"Missing {pro_path}"
    data = json.loads(pro_path.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    assert "meta" in data
    assert "schematic" in data


@pytest.mark.area_e
def test_e_t1_06_kicad8_schematic_sexpr_structure(hardware_dir: Path) -> None:
    """E-T1-06: Verify ``plc500_bench.kicad_sch`` KiCad 8 S-expression (version 20231120)."""
    sch_path = hardware_dir / "kicad" / "plc500_bench.kicad_sch"
    res = validate_kicad8_sch(sch_path)
    assert res.ok, f"KiCad schematic errors: {res.errors}"

    parsed = parse_sexpr(sch_path.read_text(encoding="utf-8"))
    assert parsed[0] == "kicad_sch"
    joined_labels = " ".join(res.metadata.get("labels", []))
    for expected_lbl in ("DI0", "DI1", "DI2", "DI3", "DO0", "DO1", "DO2", "DO3"):
        assert expected_lbl in joined_labels, f"KiCad schematic missing net label {expected_lbl}"

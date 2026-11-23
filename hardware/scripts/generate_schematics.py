"""Generate PDF, 300 DPI PNG, and vector SVG diagrams from hardware LaTeX sources."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

import pypdfium2 as pdfium
from PIL import Image

REPO_ROOT = Path(__file__).resolve().parents[2]
LATEX_DIR = REPO_ROOT / "hardware" / "latex"
DIAGRAMS_DIR = REPO_ROOT / "hardware" / "diagrams"

MANIFEST: list[tuple[str, str]] = [
    ("schematic_electrical.tex", "electrical_wiring"),
    ("schematic_network_bench.tex", "network_and_bench_layout"),
]

LOG_FORBIDDEN_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("TeX error", re.compile(r"^!", re.MULTILINE)),
    ("Fatal TeX error", re.compile(r"Emergency stop|Fatal error")),
    ("Missing character", re.compile(r"Missing character:")),
    (
        "Box/Float warning",
        re.compile(r"Overfull \\[hv]box|Underfull \\[hv]box|Float too large"),
    ),
    (
        "LaTeX/Package/Class warning",
        re.compile(r"(?:LaTeX|Package\s+\S+|Class\s+\S+)\s+Warning:"),
    ),
    ("Undefined reference", re.compile(r"There were undefined references|Citation `.*' on page .* undefined")),
]


def resolve_tex_binary(name: str) -> Path:
    """Resolve TeX Live / TinyTeX executable without relying on interactive PATH."""
    found = shutil.which(name)
    if found:
        return Path(found)
    tinytex_bin = Path.home() / ".TinyTeX" / "bin" / "x86_64-linux" / name
    if tinytex_bin.is_file() and os.access(tinytex_bin, os.X_OK):
        return tinytex_bin
    raise FileNotFoundError(
        f"Required binary '{name}' not found in PATH or ~/.TinyTeX/bin/x86_64-linux/. "
        f"Install via ~/.TinyTeX/bin/x86_64-linux/tlmgr install {name}"
    )


def audit_tex_log(log_path: Path) -> None:
    """Audit a TeX .log file for errors, missing chars, warnings, and box overflows."""
    raw = log_path.read_text(encoding="utf-8", errors="replace")
    # Strip benign standalone Non-PDF special diagnostic emitted during pdflatex
    filtered_lines = [
        line
        for line in raw.splitlines()
        if "Non-PDF special ignored!" not in line and "<special> papersize=" not in line
    ]
    cleaned = "\n".join(filtered_lines)

    issues: list[str] = []
    for label, pattern in LOG_FORBIDDEN_PATTERNS:
        matches = pattern.findall(cleaned)
        if matches:
            issues.append(f"{label}: {matches[:3]}")
    if issues:
        raise RuntimeError(f"LaTeX log audit failed for {log_path.name}: {'; '.join(issues)}")


def render_pdf_to_png(pdf_path: Path, png_path: Path, dpi: int = 300) -> tuple[int, int]:
    """Render first page of PDF to a 300 DPI PNG via pypdfium2 + Pillow and validate."""
    doc = pdfium.PdfDocument(str(pdf_path))
    try:
        if len(doc) < 1:
            raise RuntimeError(f"PDF has 0 pages: {pdf_path}")
        page = doc.get_page(0)
        try:
            scale = dpi / 72.0
            bitmap = page.render(scale=scale)
            pil_img = bitmap.to_pil()
            pil_img.save(str(png_path), format="PNG", dpi=(dpi, dpi))
        finally:
            page.close()
    finally:
        doc.close()

    with Image.open(png_path) as img:
        img.load()
        if img.mode not in {"RGB", "RGBA"}:
            raise RuntimeError(f"Unexpected PNG color mode {img.mode} in {png_path}")
        width, height = img.size
        if width <= 0 or height <= 0:
            raise RuntimeError(f"Invalid PNG dimensions {img.size} in {png_path}")
        extrema = img.getextrema()
        if not any(lo < hi for lo, hi in extrema[:3]):
            raise RuntimeError(f"Blank PNG detected: {png_path}")
    return width, height


def validate_svg(svg_path: Path) -> None:
    """Verify generated SVG is well-formed XML with positive viewport and vector elements."""
    tree = ET.parse(svg_path)
    root = tree.getroot()
    if not root.tag.endswith("svg"):
        raise RuntimeError(f"Root tag is not <svg>: {root.tag}")
    view_box = root.attrib.get("viewBox", "")
    if not view_box:
        raise RuntimeError(f"SVG missing viewBox attribute: {svg_path}")
    parts = [float(p) for p in view_box.replace(",", " ").split()]
    if len(parts) != 4 or parts[2] <= 0 or parts[3] <= 0:
        raise RuntimeError(f"Invalid SVG viewBox '{view_box}' in {svg_path}")

    has_vectors = any(
        elem.tag.split("}")[-1] in {"path", "g", "rect", "line", "circle", "polygon"}
        for elem in root.iter()
    )
    if not has_vectors:
        raise RuntimeError(f"SVG contains no vector graphics elements: {svg_path}")


def build_diagram(
    src_tex: Path,
    out_stem: str,
    pdflatex_bin: Path,
    latex_bin: Path,
    dvisvgm_bin: Path,
) -> dict[str, object]:
    """Compile a single .tex schematic in an isolated temp dir to PDF, PNG, and SVG."""
    if not src_tex.is_file():
        raise FileNotFoundError(f"Missing LaTeX schematic source: {src_tex}")

    DIAGRAMS_DIR.mkdir(parents=True, exist_ok=True)
    out_pdf = DIAGRAMS_DIR / f"{out_stem}.pdf"
    out_png = DIAGRAMS_DIR / f"{out_stem}.png"
    out_svg = DIAGRAMS_DIR / f"{out_stem}.svg"

    with tempfile.TemporaryDirectory(prefix="plc500_sch_") as tmp_str:
        tmp_dir = Path(tmp_str)
        tex_env = os.environ.copy()
        tinytex_dir = str(pdflatex_bin.parent)
        tex_env["PATH"] = f"{tinytex_dir}:{tex_env.get('PATH', '')}"

        # Pass 1: pdflatex -> PDF
        pdf_cmd = [
            str(pdflatex_bin),
            "-interaction=nonstopmode",
            "-halt-on-error",
            f"-output-directory={tmp_dir}",
            str(src_tex),
        ]
        res_pdf = subprocess.run(
            pdf_cmd,
            cwd=str(LATEX_DIR),
            env=tex_env,
            capture_output=True,
            text=True,
            check=False,
        )
        if res_pdf.returncode != 0:
            raise RuntimeError(
                f"pdflatex failed for {src_tex.name} (rc={res_pdf.returncode}):\n"
                f"{res_pdf.stdout[-1500:]}\n{res_pdf.stderr[-500:]}"
            )
        pdf_log = tmp_dir / f"{src_tex.stem}.log"
        audit_tex_log(pdf_log)

        built_pdf = tmp_dir / f"{src_tex.stem}.pdf"
        if not built_pdf.is_file():
            raise RuntimeError(f"Expected PDF missing: {built_pdf}")
        shutil.copy2(built_pdf, out_pdf)

        # Pass 2: PDF -> 300 DPI PNG
        png_w, png_h = render_pdf_to_png(out_pdf, out_png, dpi=300)

        # Pass 3: latex (DVI with dvisvgm standalone option) -> dvisvgm -> SVG
        svg_job = f"{src_tex.stem}_svg"
        dvi_cmd = [
            str(latex_bin),
            "-interaction=nonstopmode",
            "-halt-on-error",
            f"-output-directory={tmp_dir}",
            f"-jobname={svg_job}",
            f"\\PassOptionsToClass{{dvisvgm}}{{standalone}}\\input{{{src_tex.as_posix()}}}",
        ]
        res_dvi = subprocess.run(
            dvi_cmd,
            cwd=str(LATEX_DIR),
            env=tex_env,
            capture_output=True,
            text=True,
            check=False,
        )
        if res_dvi.returncode != 0:
            raise RuntimeError(
                f"latex (DVI) failed for {src_tex.name} (rc={res_dvi.returncode}):\n"
                f"{res_dvi.stdout[-1500:]}\n{res_dvi.stderr[-500:]}"
            )
        dvi_log = tmp_dir / f"{svg_job}.log"
        audit_tex_log(dvi_log)

        built_dvi = tmp_dir / f"{svg_job}.dvi"
        svg_cmd = [
            str(dvisvgm_bin),
            "--no-fonts",
            "--exact-bbox",
            "-o",
            str(out_svg),
            str(built_dvi),
        ]
        res_svg = subprocess.run(
            svg_cmd,
            cwd=str(tmp_dir),
            env=tex_env,
            capture_output=True,
            text=True,
            check=False,
        )
        if res_svg.returncode != 0:
            raise RuntimeError(
                f"dvisvgm failed for {src_tex.name} (rc={res_svg.returncode}):\n"
                f"{res_svg.stdout}\n{res_svg.stderr}"
            )
        validate_svg(out_svg)

    return {
        "source": src_tex.name,
        "pdf": str(out_pdf.relative_to(REPO_ROOT)),
        "png": str(out_png.relative_to(REPO_ROOT)),
        "svg": str(out_svg.relative_to(REPO_ROOT)),
        "png_size": f"{png_w}x{png_h}",
    }


def main() -> int:
    """Compile all hardware schematics and report verification summary."""
    pdflatex_bin = resolve_tex_binary("pdflatex")
    latex_bin = resolve_tex_binary("latex")
    dvisvgm_bin = resolve_tex_binary("dvisvgm")

    for tex_name, out_stem in MANIFEST:
        summary = build_diagram(
            LATEX_DIR / tex_name,
            out_stem,
            pdflatex_bin,
            latex_bin,
            dvisvgm_bin,
        )
        print(
            f"[OK] {summary['source']} -> {summary['pdf']}, "
            f"{summary['png']} ({summary['png_size']} @ 300 DPI), {summary['svg']} "
            "(0 errors, 0 box warnings)"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())

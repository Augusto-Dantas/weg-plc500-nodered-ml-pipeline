"""Pure-Python PDF, PNG (300 DPI), and SVG diagram validators."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

import pypdfium2 as pdfium
from PIL import Image, ImageStat

from . import ValidationResult

_SVG_PRIMITIVES = {
    "path",
    "rect",
    "circle",
    "ellipse",
    "line",
    "polyline",
    "polygon",
    "text",
}


def validate_pdf_diagram(pdf_path: Path, min_pages: int = 1) -> ValidationResult:
    """Validate PDF magic header, page count, and non-blank rendered pixel variance."""
    if not pdf_path.is_file():
        return ValidationResult(ok=False, errors=[f"Missing PDF diagram: {pdf_path}"])
    raw_head = pdf_path.read_bytes()[:8]
    if not raw_head.startswith(b"%PDF-"):
        return ValidationResult(ok=False, errors=[f"{pdf_path.name} missing %PDF- header"])

    errors: list[str] = []
    metadata: dict[str, object] = {}
    doc = None
    try:
        doc = pdfium.PdfDocument(str(pdf_path))
        page_count = len(doc)
        metadata["pages"] = page_count
        if page_count < min_pages:
            errors.append(f"Expected >= {min_pages} PDF pages, got {page_count}")
        if page_count >= 1:
            page = doc[0]
            bitmap = page.render(scale=300 / 72)
            pil_img = bitmap.to_pil().convert("RGB")
            metadata["render_300dpi_size"] = pil_img.size
            max_std = max(ImageStat.Stat(pil_img).stddev)
            metadata["max_rgb_stddev"] = max_std
            if max_std <= 5.0:
                errors.append(f"PDF page 0 appears blank/solid (max_std={max_std:.2f})")
    except Exception as exc:  # noqa: BLE001
        errors.append(f"PDF render failed for {pdf_path.name}: {exc}")
    finally:
        if doc is not None:
            doc.close()

    return ValidationResult(ok=not errors, errors=errors, metadata=metadata)


def validate_png_diagram(
    png_path: Path,
    matching_pdf: Path | None = None,
    min_width: int = 800,
    min_height: int = 400,
) -> ValidationResult:
    """Validate PNG integrity, resolution/DPI, pixel variance, and ink fraction."""
    if not png_path.is_file():
        return ValidationResult(ok=False, errors=[f"Missing PNG diagram: {png_path}"])

    errors: list[str] = []
    metadata: dict[str, object] = {}
    try:
        with Image.open(png_path) as check_img:
            check_img.verify()

        with Image.open(png_path) as img:
            width, height = img.size
            dpi = img.info.get("dpi")
            rgb = img.convert("RGB")
            gray = rgb.convert("L")
            max_std = max(ImageStat.Stat(rgb).stddev)
            # Count non-white pixels (luminance < 250)
            hist = gray.histogram()
            total_px = width * height
            non_white = sum(hist[:250])
            ink_frac = (non_white / total_px) if total_px > 0 else 0.0

            metadata.update(
                {
                    "size": (width, height),
                    "dpi": dpi,
                    "max_rgb_stddev": max_std,
                    "ink_fraction": ink_frac,
                }
            )

            if width < min_width or height < min_height:
                errors.append(
                    f"PNG dimensions {(width, height)} below minimum {(min_width, min_height)}"
                )
            if max_std <= 5.0:
                errors.append(f"PNG appears blank or solid (max_rgb_stddev={max_std:.2f})")
            if not (0.005 < ink_frac < 0.95):
                errors.append(
                    f"PNG ink fraction {ink_frac:.4f} outside healthy range (0.005, 0.95)"
                )

            dpi_ok = (
                isinstance(dpi, tuple)
                and len(dpi) >= 2
                and float(dpi[0]) >= 299.0
                and float(dpi[1]) >= 299.0
            )
            if not dpi_ok and matching_pdf is not None and matching_pdf.is_file():
                pdf_res = validate_pdf_diagram(matching_pdf)
                pdf_size = pdf_res.metadata.get("render_300dpi_size")
                if (
                    isinstance(pdf_size, tuple)
                    and len(pdf_size) == 2
                    and abs(width - pdf_size[0]) <= 5
                    and abs(height - pdf_size[1]) <= 5
                ):
                    dpi_ok = True
            if not dpi_ok:
                errors.append(
                    f"PNG DPI/size check failed (dpi={dpi}, size={(width, height)})"
                )
    except Exception as exc:  # noqa: BLE001
        errors.append(f"PNG validation error for {png_path.name}: {exc}")

    return ValidationResult(ok=not errors, errors=errors, metadata=metadata)


def _parse_svg_len(val: str | None) -> float | None:
    if not val:
        return None
    cleaned = val.strip().lower()
    for suffix in ("px", "pt", "mm", "cm", "in"):
        if cleaned.endswith(suffix):
            cleaned = cleaned[: -len(suffix)].strip()
            break
    try:
        num = float(cleaned)
        return num if num > 0 else None
    except ValueError:
        return None


def validate_svg_diagram(svg_path: Path, min_elements: int = 5) -> ValidationResult:
    """Validate SVG XML well-formedness, viewBox/dimensions, and vector primitives."""
    if not svg_path.is_file():
        return ValidationResult(ok=False, errors=[f"Missing SVG diagram: {svg_path}"])

    errors: list[str] = []
    metadata: dict[str, object] = {}
    try:
        root = ET.fromstring(svg_path.read_text(encoding="utf-8"))
    except ET.ParseError as exc:
        return ValidationResult(ok=False, errors=[f"SVG XML parse error: {exc}"])

    local_root = root.tag.split("}", 1)[1] if "}" in root.tag else root.tag
    if local_root != "svg":
        errors.append(f"Expected <svg> root, got <{local_root}>")

    vb_raw = (root.attrib.get("viewBox") or "").strip()
    bounds_ok = False
    if vb_raw:
        parts = vb_raw.replace(",", " ").split()
        if len(parts) == 4:
            try:
                _, _, w, h = (float(p) for p in parts)
                if w > 0 and h > 0:
                    bounds_ok = True
                    metadata["viewBox"] = (w, h)
            except ValueError:
                pass
    if not bounds_ok:
        w = _parse_svg_len(root.attrib.get("width"))
        h = _parse_svg_len(root.attrib.get("height"))
        if w is not None and h is not None:
            bounds_ok = True
            metadata["dimensions"] = (w, h)
    if not bounds_ok:
        errors.append("SVG missing positive viewBox or width/height")

    prim_count = 0
    for elem in root.iter():
        lname = elem.tag.split("}", 1)[1] if "}" in elem.tag else elem.tag
        if lname in _SVG_PRIMITIVES:
            prim_count += 1
    metadata["primitive_count"] = prim_count
    if prim_count < min_elements:
        errors.append(f"SVG contains only {prim_count} geometry primitives (< {min_elements})")

    return ValidationResult(ok=not errors, errors=errors, metadata=metadata)

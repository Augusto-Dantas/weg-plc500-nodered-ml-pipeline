"""Pure-Python PLCopen TC6 XML v2.01 validator (no xmllint dependency)."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path

from . import ValidationResult

TC6_NS = "http://www.plcopen.org/xml/tc6_0201"
EXPECTED_POUS: dict[str, str] = {
    "FB_DebounceCounter": "functionBlock",
    "FB_WatchdogHeartbeat": "functionBlock",
    "FB_SafetyInterlock": "functionBlock",
    "PLC_PRG": "program",
}
_REQUIRED_GLOBAL_VARS = (
    "xDI_Presenca",
    "xCmd_Motor",
    "uiCnt_Presenca",
    "uiTimeout_WD_ms",
    "uiVersao_Mapa",
)


def _local_name(tag: str) -> str:
    return tag.split("}", 1)[1] if "}" in tag else tag


def _parse_interval_ms(raw: str) -> float | None:
    text = raw.strip().upper()
    if not text:
        return None
    # ISO-8601 duration: PT0.01S, PT0.010S, PT10MS
    iso_s = re.fullmatch(r"PT(\d+(?:\.\d+)?)S", text)
    if iso_s:
        return float(iso_s.group(1)) * 1000.0
    iso_ms = re.fullmatch(r"PT(\d+(?:\.\d+)?)MS", text)
    if iso_ms:
        return float(iso_ms.group(1))
    # IEC duration: T#10MS, TIME#10MS, 10MS
    iec_ms = re.fullmatch(r"(?:T#|TIME#)?(\d+(?:\.\d+)?)MS", text)
    if iec_ms:
        return float(iec_ms.group(1))
    iec_s = re.fullmatch(r"(?:T#|TIME#)?(\d+(?:\.\d+)?)S", text)
    if iec_s:
        return float(iec_s.group(1)) * 1000.0
    return None


def validate_plcopen_xml(xml_input: Path | str) -> ValidationResult:
    """Validate a PLCopen TC6 v2.01 XML file or XML text string."""
    errors: list[str] = []
    warnings: list[str] = []
    metadata: dict[str, object] = {}

    if isinstance(xml_input, Path):
        if not xml_input.is_file():
            return ValidationResult(ok=False, errors=[f"Missing PLCopen XML: {xml_input}"])
        xml_text = xml_input.read_text(encoding="utf-8")
    else:
        xml_text = xml_input

    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        return ValidationResult(ok=False, errors=[f"XML parse error: {exc}"])

    expected_root = f"{{{TC6_NS}}}project"
    if root.tag != expected_root:
        errors.append(f"Expected root tag {expected_root!r}, got {root.tag!r}")

    # Find elements namespace-agnostically after checking root namespace
    def find_first(parent: ET.Element, name: str) -> ET.Element | None:
        for elem in parent.iter():
            if _local_name(elem.tag) == name:
                return elem
        return None

    def find_all(parent: ET.Element, name: str) -> list[ET.Element]:
        return [elem for elem in parent.iter() if _local_name(elem.tag) == name]

    file_header = find_first(root, "fileHeader")
    if file_header is None:
        errors.append("Missing <fileHeader>")
    else:
        for attr in ("companyName", "productName", "productVersion", "creationDateTime"):
            if not (file_header.attrib.get(attr) or "").strip():
                errors.append(f"<fileHeader> missing non-empty attribute {attr!r}")

    content_header = find_first(root, "contentHeader")
    if content_header is None:
        errors.append("Missing <contentHeader>")
    else:
        if not (content_header.attrib.get("name") or "").strip():
            errors.append("<contentHeader> missing non-empty attribute 'name'")
        coord_info = find_first(content_header, "coordinateInfo")
        if coord_info is None:
            errors.append("<contentHeader> missing <coordinateInfo>")
        else:
            for child_name in ("fbd", "ld", "sfc"):
                if find_first(coord_info, child_name) is None:
                    errors.append(f"<coordinateInfo> missing <{child_name}>")

    pous_elem = find_first(root, "pous")
    found_pous: dict[str, str] = {}
    if pous_elem is None:
        errors.append("Missing <types>/<pous>")
    else:
        for pou in find_all(pous_elem, "pou"):
            name = (pou.attrib.get("name") or "").strip()
            pou_type = (pou.attrib.get("pouType") or "").strip()
            if name:
                found_pous[name] = pou_type
            if name in EXPECTED_POUS:
                if pou_type != EXPECTED_POUS[name]:
                    errors.append(
                        f"POU {name!r} expected pouType={EXPECTED_POUS[name]!r}, got {pou_type!r}"
                    )
                if find_first(pou, "interface") is None:
                    errors.append(f"POU {name!r} missing <interface>")
                body = find_first(pou, "body")
                st_elem = find_first(body, "ST") if body is not None else None
                if st_elem is None:
                    errors.append(f"POU {name!r} missing <body>/<ST>")
                else:
                    st_code = "".join(st_elem.itertext()).strip()
                    if not st_code:
                        errors.append(f"POU {name!r} has empty <ST> body")

        for req_name in EXPECTED_POUS:
            if req_name not in found_pous:
                errors.append(f"Missing expected POU {req_name!r} in <pous>")

    metadata["pous"] = found_pous

    global_var_names: set[str] = set()
    for gvars in find_all(root, "globalVars"):
        for var in find_all(gvars, "variable"):
            vname = (var.attrib.get("name") or "").strip()
            if vname:
                global_var_names.add(vname)
    for req_var in _REQUIRED_GLOBAL_VARS:
        if req_var not in global_var_names:
            errors.append(f"Missing Modbus global variable {req_var!r} in <globalVars>")
    metadata["global_vars_count"] = len(global_var_names)

    tasks = find_all(root, "task")
    main_task = next((t for t in tasks if t.attrib.get("name") == "MainTask"), None)
    if main_task is None:
        errors.append("Missing <task name='MainTask'> in <instances>")
    else:
        raw_interval = (main_task.attrib.get("interval") or "").strip()
        interval_ms = _parse_interval_ms(raw_interval)
        metadata["main_task_interval_ms"] = interval_ms
        if interval_ms is None or abs(interval_ms - 10.0) > 1e-6:
            errors.append(
                f"MainTask interval expected 10 ms (e.g. PT0.01S), got {raw_interval!r}"
            )
        task_pous = find_all(main_task, "pouInstance")
        has_prg = any(
            p.attrib.get("name") == "PLC_PRG" or p.attrib.get("typeName") == "PLC_PRG"
            for p in task_pous
        )
        if not has_prg:
            # Also allow resource-level pouInstance if task references it
            all_instances = find_all(root, "pouInstance")
            has_prg = any(
                p.attrib.get("name") == "PLC_PRG" or p.attrib.get("typeName") == "PLC_PRG"
                for p in all_instances
            )
            if not has_prg:
                errors.append("MainTask missing <pouInstance> referencing PLC_PRG")
            else:
                warnings.append("PLC_PRG pouInstance found outside MainTask element")

    return ValidationResult(
        ok=not errors,
        errors=errors,
        warnings=warnings,
        metadata=metadata,
    )

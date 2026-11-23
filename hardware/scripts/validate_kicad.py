"""Pure-Python 5-layer validator for KiCad 8 schematic & project files."""

from __future__ import annotations

import json
import math
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SCH_PATH = REPO_ROOT / "hardware" / "kicad" / "plc500_bench.kicad_sch"
DEFAULT_PRO_PATH = REPO_ROOT / "hardware" / "kicad" / "plc500_bench.kicad_pro"

GRID_MM = 1.27
GRID_TOL = 1e-4
PIN_WIRE_TOL = 1e-3


@dataclass(frozen=True)
class SAtom:
    """Represents an unquoted S-expression atom/symbol/number."""

    value: str


@dataclass(frozen=True)
class SString:
    """Represents a double-quoted S-expression string."""

    value: str


SExpr = list[Any] | SAtom | SString


def tokenize_sexpr(text: str) -> list[tuple[str, str]]:
    """Tokenize KiCad S-expression text into ('LPAREN'|'RPAREN'|'STRING'|'ATOM', val)."""
    tokens: list[tuple[str, str]] = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch.isspace():
            i += 1
            continue
        if ch == ";":
            # Line comment if present
            while i < n and text[i] != "\n":
                i += 1
            continue
        if ch == "(":
            tokens.append(("LPAREN", "("))
            i += 1
        elif ch == ")":
            tokens.append(("RPAREN", ")"))
            i += 1
        elif ch == '"':
            i += 1
            buf: list[str] = []
            closed = False
            while i < n:
                c = text[i]
                if c == "\\":
                    i += 1
                    if i >= n:
                        raise ValueError("Unterminated escape inside quoted string")
                    esc = text[i]
                    if esc == "n":
                        buf.append("\n")
                    elif esc == "t":
                        buf.append("\t")
                    else:
                        buf.append(esc)
                    i += 1
                elif c == '"':
                    closed = True
                    i += 1
                    break
                else:
                    buf.append(c)
                    i += 1
            if not closed:
                raise ValueError("Unclosed string literal in S-expression")
            tokens.append(("STRING", "".join(buf)))
        else:
            start = i
            while i < n and not text[i].isspace() and text[i] not in '()"':
                i += 1
            tokens.append(("ATOM", text[start:i]))
    return tokens


def parse_sexpr(text: str) -> list[Any]:
    """L1: Parse S-expression text into nested lists with strict balance checking."""
    tokens = tokenize_sexpr(text)
    stack: list[list[Any]] = []
    roots: list[Any] = []
    for kind, val in tokens:
        if kind == "LPAREN":
            new_list: list[Any] = []
            if stack:
                stack[-1].append(new_list)
            else:
                roots.append(new_list)
            stack.append(new_list)
        elif kind == "RPAREN":
            if not stack:
                raise ValueError("Unexpected closing parenthesis ')' (depth < 0)")
            stack.pop()
        elif kind == "STRING":
            if not stack:
                raise ValueError(f"Top-level bare string {val!r} outside list")
            stack[-1].append(SString(val))
        else:
            if not stack:
                raise ValueError(f"Top-level bare atom {val!r} outside list")
            stack[-1].append(SAtom(val))

    if stack:
        raise ValueError(f"Unclosed parentheses: depth={len(stack)} at EOF")
    if len(roots) != 1:
        raise ValueError(f"Expected exactly 1 top-level S-expression, found {len(roots)}")
    root = roots[0]
    if not isinstance(root, list) or not root or not _is_atom(root[0], "kicad_sch"):
        raise ValueError("Root S-expression must start with 'kicad_sch'")
    return root


def _val(node: Any) -> str:
    if isinstance(node, (SAtom, SString)):
        return node.value
    raise TypeError(f"Expected scalar S-expression node, got {type(node).__name__}")


def _is_atom(node: Any, expected: str) -> bool:
    return isinstance(node, SAtom) and node.value == expected


def _head(node: Any) -> str | None:
    if isinstance(node, list) and node and isinstance(node[0], SAtom):
        return node[0].value
    return None


def _find_first(lst: list[Any], tag: str) -> list[Any] | None:
    for item in lst:
        if _head(item) == tag:
            return item
    return None


def _find_all(lst: list[Any], tag: str) -> list[list[Any]]:
    return [item for item in lst if _head(item) == tag]


def _parse_xy(node: list[Any]) -> tuple[float, float]:
    return float(_val(node[1])), float(_val(node[2]))


def _parse_at(node: list[Any]) -> tuple[float, float, float]:
    x = float(_val(node[1]))
    y = float(_val(node[2]))
    angle = float(_val(node[3])) if len(node) > 3 else 0.0
    return x, y, angle


def _collect_uuids(node: Any, out: list[str]) -> None:
    if isinstance(node, list):
        if _head(node) == "uuid" and len(node) == 2:
            out.append(_val(node[1]))
        else:
            for child in node:
                _collect_uuids(child, out)


def _check_grid(x: float, y: float, label: str) -> None:
    rx = abs(x / GRID_MM - round(x / GRID_MM)) * GRID_MM
    ry = abs(y / GRID_MM - round(y / GRID_MM)) * GRID_MM
    if rx > GRID_TOL or ry > GRID_TOL:
        raise ValueError(f"{label} ({x:.4f}, {y:.4f}) is off {GRID_MM} mm grid")


def _pin_world_coords(
    sym_x: float,
    sym_y: float,
    sym_angle: float,
    px: float,
    py: float,
) -> tuple[float, float]:
    """Convert KiCad 8 lib_symbol pin (Y-up) to sheet world coords (Y-down)."""
    ang = round(sym_angle) % 360
    if ang == 0:
        return sym_x + px, sym_y - py
    if ang == 90:
        return sym_x - py, sym_y - px
    if ang == 180:
        return sym_x - px, sym_y + py
    if ang == 270:
        return sym_x + py, sym_y + px
    raise ValueError(f"Unsupported symbol rotation angle {sym_angle}°")


def _point_on_segment(
    px: float,
    py: float,
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    tol: float = PIN_WIRE_TOL,
) -> bool:
    """Return True if (px, py) lies on closed segment (x1,y1)-(x2,y2) within tol."""
    dx = x2 - x1
    dy = y2 - y1
    seg_len_sq = dx * dx + dy * dy
    if seg_len_sq < 1e-12:
        return math.hypot(px - x1, py - y1) <= tol
    t = ((px - x1) * dx + (py - y1) * dy) / seg_len_sq
    if t < -1e-6 or t > 1.0 + 1e-6:
        return False
    t_clamped = max(0.0, min(1.0, t))
    proj_x = x1 + t_clamped * dx
    proj_y = y1 + t_clamped * dy
    return math.hypot(px - proj_x, py - proj_y) <= tol


def validate_kicad(
    sch_path: Path = DEFAULT_SCH_PATH,
    pro_path: Path = DEFAULT_PRO_PATH,
) -> dict[str, int]:
    """Execute L1-L5 validation on KiCad 8 schematic & project files."""
    if not sch_path.is_file():
        raise FileNotFoundError(f"Schematic file not found: {sch_path}")
    if not pro_path.is_file():
        raise FileNotFoundError(f"Project file not found: {pro_path}")

    # L1: Balanced S-Expression AST
    sch_text = sch_path.read_text(encoding="utf-8")
    root = parse_sexpr(sch_text)

    # L2: KiCad 8 Header & UUID Uniqueness
    version_node = _find_first(root, "version")
    if version_node is None or _val(version_node[1]) != "20231120":
        raise ValueError("Missing or invalid (version 20231120)")

    gen_ver_node = _find_first(root, "generator_version")
    if gen_ver_node is None or _val(gen_ver_node[1]) != "8.0":
        raise ValueError('Missing or invalid (generator_version "8.0")')

    paper_node = _find_first(root, "paper")
    if paper_node is None or _val(paper_node[1]) != "A3":
        raise ValueError('Missing or invalid (paper "A3")')

    for req_section in ("title_block", "lib_symbols", "sheet_instances"):
        if _find_first(root, req_section) is None:
            raise ValueError(f"Missing required top-level section ({req_section} ...)")

    sheet_inst = _find_first(root, "sheet_instances")
    assert sheet_inst is not None
    path_node = _find_first(sheet_inst, "path")
    if path_node is None or _val(path_node[1]) != "/":
        raise ValueError('Missing root (path "/" (page "1")) in sheet_instances')

    root_uuid_node = _find_first(root, "uuid")
    if root_uuid_node is None:
        raise ValueError("Missing top-level (uuid ...)")
    root_uuid = _val(root_uuid_node[1])

    all_uuids: list[str] = []
    _collect_uuids(root, all_uuids)
    if not all_uuids:
        raise ValueError("No UUIDs found in schematic")
    seen_uuids: set[str] = set()
    for u in all_uuids:
        uuid.UUID(u)  # validates RFC 4122 format
        if u in seen_uuids:
            raise ValueError(f"Duplicate UUID detected: {u}")
        seen_uuids.add(u)

    # L3: .kicad_pro JSON Validation
    pro_data = json.loads(pro_path.read_text(encoding="utf-8"))
    meta = pro_data.get("meta", {})
    if meta.get("filename") != pro_path.name:
        raise ValueError(f".kicad_pro meta.filename mismatch: {meta.get('filename')}")
    if int(meta.get("version", 0)) < 1:
        raise ValueError(".kicad_pro meta.version must be >= 1")
    sheets = pro_data.get("sheets", [])
    if not sheets or sheets[0][0] != root_uuid:
        raise ValueError(".kicad_pro sheets[0][0] does not match schematic root UUID")
    if not pro_data.get("net_settings", {}).get("classes"):
        raise ValueError(".kicad_pro missing net_settings.classes")
    if "schematic" not in pro_data:
        raise ValueError(".kicad_pro missing schematic section")

    # L4: lib_symbols & Placed Symbol Consistency
    lib_symbols_node = _find_first(root, "lib_symbols")
    assert lib_symbols_node is not None
    lib_pins: dict[str, dict[str, tuple[float, float]]] = {}
    for sym_def in _find_all(lib_symbols_node, "symbol"):
        lib_id = _val(sym_def[1])
        pins_map: dict[str, tuple[float, float]] = {}
        for sub_sym in _find_all(sym_def, "symbol"):
            for pin_node in _find_all(sub_sym, "pin"):
                at_node = _find_first(pin_node, "at")
                num_node = _find_first(pin_node, "number")
                if at_node is None or num_node is None:
                    raise ValueError(f"Malformed pin in lib_symbol {lib_id}")
                px, py, _ = _parse_at(at_node)
                pnum = _val(num_node[1])
                pins_map[pnum] = (px, py)
        if not pins_map:
            raise ValueError(f"lib_symbol {lib_id} defines zero pins")
        lib_pins[lib_id] = pins_map

    placed_symbols = _find_all(root, "symbol")
    if not placed_symbols:
        raise ValueError("Schematic contains zero placed symbols")

    seen_refs: set[str] = set()
    world_pins: list[tuple[str, str, float, float]] = []

    for sym in placed_symbols:
        lib_id_node = _find_first(sym, "lib_id")
        at_node = _find_first(sym, "at")
        unit_node = _find_first(sym, "unit")
        uuid_node = _find_first(sym, "uuid")
        inst_node = _find_first(sym, "instances")
        if None in (lib_id_node, at_node, unit_node, uuid_node, inst_node):
            raise ValueError("Placed symbol missing required fields (lib_id/at/unit/uuid/instances)")
        assert lib_id_node is not None and at_node is not None

        lib_id = _val(lib_id_node[1])
        if lib_id not in lib_pins:
            raise ValueError(f"Placed symbol references unknown lib_id: {lib_id}")

        sx, sy, sang = _parse_at(at_node)
        _check_grid(sx, sy, f"Symbol {lib_id}")

        props = {_val(p[1]): _val(p[2]) for p in _find_all(sym, "property") if len(p) >= 3}
        ref = props.get("Reference")
        val = props.get("Value")
        if not ref or not val:
            raise ValueError(f"Placed symbol {lib_id} missing Reference or Value property")
        if ref in seen_refs:
            raise ValueError(f"Duplicate component Reference: {ref}")
        seen_refs.add(ref)

        inst_pins = {_val(p[1]) for p in _find_all(sym, "pin")}
        expected_pins = set(lib_pins[lib_id].keys())
        if inst_pins != expected_pins:
            raise ValueError(
                f"Symbol {ref} ({lib_id}) pin mismatch: inst={sorted(inst_pins)} "
                f"vs lib={sorted(expected_pins)}"
            )

        for pnum, (px, py) in lib_pins[lib_id].items():
            wx, wy = _pin_world_coords(sx, sy, sang, px, py)
            _check_grid(wx, wy, f"Pin {ref}.{pnum}")
            world_pins.append((ref, pnum, wx, wy))

    # L5: Grid & Pin-to-Wire Connectivity Graph
    wires: list[tuple[float, float, float, float]] = []
    for wire_node in _find_all(root, "wire"):
        pts_node = _find_first(wire_node, "pts")
        if pts_node is None:
            raise ValueError("Wire missing (pts ...)")
        xy_nodes = _find_all(pts_node, "xy")
        if len(xy_nodes) != 2:
            raise ValueError(f"Wire must have 2 xy points, got {len(xy_nodes)}")
        x1, y1 = _parse_xy(xy_nodes[0])
        x2, y2 = _parse_xy(xy_nodes[1])
        _check_grid(x1, y1, "Wire start")
        _check_grid(x2, y2, "Wire end")
        wires.append((x1, y1, x2, y2))

    junctions: list[tuple[float, float]] = []
    for jnode in _find_all(root, "junction"):
        at_node = _find_first(jnode, "at")
        assert at_node is not None
        jx, jy, _ = _parse_at(at_node)
        _check_grid(jx, jy, "Junction")
        junctions.append((jx, jy))

    no_connects: list[tuple[float, float]] = []
    for nc_node in _find_all(root, "no_connect"):
        at_node = _find_first(nc_node, "at")
        assert at_node is not None
        nx, ny, _ = _parse_at(at_node)
        _check_grid(nx, ny, "No-connect")
        no_connects.append((nx, ny))

    labels: list[tuple[str, float, float]] = []
    for tag in ("label", "global_label"):
        for lnode in _find_all(root, tag):
            lname = _val(lnode[1])
            at_node = _find_first(lnode, "at")
            assert at_node is not None
            lx, ly, _ = _parse_at(at_node)
            _check_grid(lx, ly, f"{tag} {lname}")
            labels.append((lname, lx, ly))

    def _touches_wire_endpoint(x: float, y: float) -> bool:
        return any(
            math.hypot(x - x1, y - y1) <= PIN_WIRE_TOL or math.hypot(x - x2, y - y2) <= PIN_WIRE_TOL
            for x1, y1, x2, y2 in wires
        )

    def _touches_wire_segment(x: float, y: float) -> bool:
        return any(_point_on_segment(x, y, x1, y1, x2, y2) for x1, y1, x2, y2 in wires)

    # Every pin must either touch a wire endpoint OR have an explicit no_connect (mutually exclusive)
    wired_count = 0
    nc_count = 0
    for ref, pnum, wx, wy in world_pins:
        on_wire = _touches_wire_endpoint(wx, wy)
        on_nc = any(math.hypot(wx - nx, wy - ny) <= PIN_WIRE_TOL for nx, ny in no_connects)
        if on_wire and on_nc:
            raise ValueError(f"Pin {ref}.{pnum} has both a wire and a no_connect")
        if not on_wire and not on_nc:
            raise ValueError(f"Dangling pin {ref}.{pnum} at ({wx:.2f}, {wy:.2f})")
        if on_wire:
            wired_count += 1
        else:
            nc_count += 1

    # Every no_connect must correspond to a real pin
    for nx, ny in no_connects:
        if not any(math.hypot(nx - wx, ny - wy) <= PIN_WIRE_TOL for _, _, wx, wy in world_pins):
            raise ValueError(f"Orphan no_connect at ({nx:.2f}, {ny:.2f})")

    # Every label/global_label must sit on a wire segment or pin
    for lname, lx, ly in labels:
        on_pin = any(math.hypot(lx - wx, ly - wy) <= PIN_WIRE_TOL for _, _, wx, wy in world_pins)
        if not on_pin and not _touches_wire_segment(lx, ly):
            raise ValueError(f"Floating label {lname!r} at ({lx:.2f}, {ly:.2f})")

    # Every wire endpoint must connect to >=1 pin, label, junction, or another wire endpoint
    for idx, (x1, y1, x2, y2) in enumerate(wires):
        for end_name, ex, ey in (("start", x1, y1), ("end", x2, y2)):
            touches_pin = any(
                math.hypot(ex - wx, ey - wy) <= PIN_WIRE_TOL for _, _, wx, wy in world_pins
            )
            touches_lbl = any(
                math.hypot(ex - lx, ey - ly) <= PIN_WIRE_TOL for _, lx, ly in labels
            )
            touches_junc = any(
                math.hypot(ex - jx, ey - jy) <= PIN_WIRE_TOL for jx, jy in junctions
            )
            touches_other_wire = any(
                other_idx != idx
                and (
                    math.hypot(ex - ox1, ey - oy1) <= PIN_WIRE_TOL
                    or math.hypot(ex - ox2, ey - oy2) <= PIN_WIRE_TOL
                )
                for other_idx, (ox1, oy1, ox2, oy2) in enumerate(wires)
            )
            if not (touches_pin or touches_lbl or touches_junc or touches_other_wire):
                raise ValueError(
                    f"Dangling wire {end_name} at ({ex:.2f}, {ey:.2f})"
                )

    return {
        "symbols": len(placed_symbols),
        "pins_total": len(world_pins),
        "pins_wired": wired_count,
        "pins_no_connect": nc_count,
        "wires": len(wires),
        "junctions": len(junctions),
        "labels": len(labels),
        "uuids": len(all_uuids),
    }


def main() -> int:
    """Run L1-L5 KiCad 8 validation and print summary."""
    summary = validate_kicad()
    print(
        "[OK] KiCad 8 L1-L5 validation passed: "
        f"{summary['symbols']} symbols, "
        f"{summary['pins_total']}/{summary['pins_total']} pins accounted for "
        f"({summary['pins_wired']} wired + {summary['pins_no_connect']} no_connect, 0 dangling), "
        f"{summary['wires']} wires, {summary['junctions']} junctions, "
        f"{summary['labels']} labels, {summary['uuids']} unique UUIDs."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

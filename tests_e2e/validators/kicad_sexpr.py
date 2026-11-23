"""Pure-Python stack-based S-expression parser & KiCad 8 schematic validator."""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import ValidationResult


class SExprParseError(ValueError):
    """Raised when S-expression tokenization or parsing fails."""

    def __init__(self, message: str, line: int = 1, col: int = 1) -> None:
        super().__init__(f"{message} (line {line}, col {col})")
        self.line = line
        self.col = col


@dataclass(frozen=True)
class _Token:
    kind: str  # LPAREN | RPAREN | STRING | ATOM
    value: str
    line: int
    col: int


def _tokenize_sexpr(text: str) -> list[_Token]:
    tokens: list[_Token] = []
    i = 0
    n = len(text)
    line = 1
    col = 1

    while i < n:
        ch = text[i]
        if ch in " \t\r\n":
            if ch == "\n":
                line += 1
                col = 1
            else:
                col += 1
            i += 1
            continue

        if ch == ";":
            while i < n and text[i] != "\n":
                i += 1
            continue

        if ch == "(":
            tokens.append(_Token("LPAREN", "(", line, col))
            i += 1
            col += 1
            continue

        if ch == ")":
            tokens.append(_Token("RPAREN", ")", line, col))
            i += 1
            col += 1
            continue

        if ch == '"':
            start_line, start_col = line, col
            i += 1
            col += 1
            buf: list[str] = []
            closed = False
            while i < n:
                c = text[i]
                if c == "\\":
                    if i + 1 >= n:
                        raise SExprParseError("Unterminated escape in string", line, col)
                    esc = text[i + 1]
                    mapping = {"n": "\n", "r": "\r", "t": "\t", '"': '"', "\\": "\\"}
                    buf.append(mapping.get(esc, esc))
                    i += 2
                    col += 2
                    continue
                if c == '"':
                    closed = True
                    i += 1
                    col += 1
                    break
                if c == "\n":
                    line += 1
                    col = 1
                else:
                    col += 1
                buf.append(c)
                i += 1
            if not closed:
                raise SExprParseError("Unterminated string literal", start_line, start_col)
            tokens.append(_Token("STRING", "".join(buf), start_line, start_col))
            continue

        start_line, start_col = line, col
        start_i = i
        while i < n and text[i] not in " \t\r\n()\"":
            i += 1
            col += 1
        tokens.append(_Token("ATOM", text[start_i:i], start_line, start_col))

    return tokens


def parse_sexpr(text: str, strict_single_root: bool = True) -> list[Any]:
    """Iteratively parse S-expression ``text`` into nested Python lists."""
    tokens = _tokenize_sexpr(text)
    if not tokens:
        raise SExprParseError("Empty S-expression input", 1, 1)

    stack: list[list[Any]] = []
    roots: list[Any] = []
    open_locs: list[tuple[int, int]] = []

    for tok in tokens:
        if tok.kind == "LPAREN":
            new_list: list[Any] = []
            stack.append(new_list)
            open_locs.append((tok.line, tok.col))
        elif tok.kind == "RPAREN":
            if not stack:
                raise SExprParseError("Unexpected ')'", tok.line, tok.col)
            finished = stack.pop()
            open_locs.pop()
            if stack:
                stack[-1].append(finished)
            else:
                roots.append(finished)
        else:
            if not stack:
                if strict_single_root:
                    raise SExprParseError(
                        f"Unexpected top-level atom {tok.value!r}", tok.line, tok.col
                    )
                roots.append(tok.value)
            else:
                stack[-1].append(tok.value)

    if stack:
        line, col = open_locs[-1]
        raise SExprParseError("Unclosed '('", line, col)

    if strict_single_root and len(roots) != 1:
        raise SExprParseError(f"Expected 1 root S-expression, found {len(roots)}", 1, 1)

    return roots[0] if strict_single_root else roots


def _find_child(node: list[Any], head: str) -> list[Any] | None:
    for item in node[1:]:
        if isinstance(item, list) and item and item[0] == head:
            return item
    return None


def _find_children(node: list[Any], head: str) -> list[list[Any]]:
    return [item for item in node[1:] if isinstance(item, list) and item and item[0] == head]


def _is_finite_num(val: Any) -> bool:
    try:
        return math.isfinite(float(val))
    except (TypeError, ValueError):
        return False


def validate_kicad8_sch(path_or_text: Path | str) -> ValidationResult:
    """Validate KiCad 8 schematic S-expression (version 20231120) structure & references."""
    if isinstance(path_or_text, Path):
        if not path_or_text.is_file():
            return ValidationResult(ok=False, errors=[f"Missing KiCad schematic: {path_or_text}"])
        text = path_or_text.read_text(encoding="utf-8")
    else:
        text = path_or_text

    try:
        root = parse_sexpr(text, strict_single_root=True)
    except SExprParseError as exc:
        return ValidationResult(ok=False, errors=[f"S-expression parse error: {exc}"])

    errors: list[str] = []
    if not isinstance(root, list) or not root or root[0] != "kicad_sch":
        errors.append("Root S-expression must start with 'kicad_sch'")
        return ValidationResult(ok=False, errors=errors)

    ver_node = _find_child(root, "version")
    if ver_node is None or len(ver_node) < 2:
        errors.append("Missing (version ...)")
    elif str(ver_node[1]) != "20231120":
        errors.append(f"Expected KiCad 8 version 20231120, got {ver_node[1]!r}")

    for req_head in ("generator", "uuid", "lib_symbols", "sheet_instances"):
        if _find_child(root, req_head) is None:
            errors.append(f"Missing ({req_head} ...)")

    defined_lib_ids: set[str] = set()
    lib_symbols = _find_child(root, "lib_symbols")
    if lib_symbols is not None:
        for sym in _find_children(lib_symbols, "symbol"):
            if len(sym) >= 2 and isinstance(sym[1], str):
                defined_lib_ids.add(sym[1])
    if not defined_lib_ids:
        errors.append("(lib_symbols ...) contains no symbol definitions")

    placed_symbols = _find_children(root, "symbol")
    wires = _find_children(root, "wire")
    labels = (
        _find_children(root, "label")
        + _find_children(root, "global_label")
        + _find_children(root, "hierarchical_label")
    )

    if len(placed_symbols) < 5:
        errors.append(f"Expected >= 5 placed symbols, found {len(placed_symbols)}")
    if len(wires) < 5:
        errors.append(f"Expected >= 5 wire segments, found {len(wires)}")
    if len(labels) < 4:
        errors.append(f"Expected >= 4 net labels, found {len(labels)}")

    for idx, sym in enumerate(placed_symbols):
        lib_id_node = _find_child(sym, "lib_id")
        if lib_id_node is None or len(lib_id_node) < 2:
            errors.append(f"Placed symbol #{idx} missing (lib_id ...)")
        else:
            lib_id = str(lib_id_node[1])
            if lib_id not in defined_lib_ids:
                errors.append(f"Dangling symbol lib_id {lib_id!r} not in (lib_symbols ...)")
        at_node = _find_child(sym, "at")
        if at_node is None or len(at_node) < 3 or not (
            _is_finite_num(at_node[1]) and _is_finite_num(at_node[2])
        ):
            errors.append(f"Placed symbol #{idx} missing finite (at x y) coordinates")

    for idx, wire in enumerate(wires):
        pts_node = _find_child(wire, "pts")
        xy_nodes = _find_children(pts_node, "xy") if pts_node is not None else []
        if len(xy_nodes) < 2:
            errors.append(f"Wire #{idx} must have >= 2 (xy x y) points")
            continue
        for pt in xy_nodes:
            if len(pt) < 3 or not (_is_finite_num(pt[1]) and _is_finite_num(pt[2])):
                errors.append(f"Wire #{idx} has non-finite point coordinates: {pt}")

    label_names = [str(lbl[1]) for lbl in labels if len(lbl) >= 2]
    return ValidationResult(
        ok=not errors,
        errors=errors,
        metadata={
            "defined_lib_ids": sorted(defined_lib_ids),
            "placed_symbols": len(placed_symbols),
            "wires": len(wires),
            "labels": label_names,
        },
    )

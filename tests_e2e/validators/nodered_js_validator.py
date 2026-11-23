"""Pure-Python Node-RED flows.json topology & state-machine JS lexer validator."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import unicodedata
from pathlib import Path
from typing import Any

_EXPR_KW = {
    "return",
    "case",
    "throw",
    "typeof",
    "void",
    "delete",
    "in",
    "instanceof",
    "new",
    "await",
    "yield",
    "else",
    "do",
    "of",
}

_BANNED_PLACEHOLDER_RE = re.compile(r"\b(TODO|FIXME|XXX|TBD|PLACEHOLDER)\b")


def _fold_ascii(text: str) -> str:
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in nfkd if not unicodedata.combining(ch)).lower()


def validate_js_syntax(code: str, label: str = "<js>") -> list[str]:
    """Validate JS lexical integrity, delimiter balance, and regex/template literals."""
    errors: list[str] = []
    i = 0
    n = len(code)
    line = 1
    state = "CODE"
    stack: list[tuple[str, int]] = []
    template_brace_depths: list[int] = []
    regex_in_char_class = False
    last_sig = ""  # "", "IDENT", "NUMBER", "CLOSE_PAREN", "CLOSE_BRACKET", "STRING", "PUNCT", "KW_EXPR"

    while i < n:
        ch = code[i]
        nxt = code[i + 1] if i + 1 < n else ""

        if state == "LINE_COMMENT":
            if ch == "\n":
                state = "CODE"
                line += 1
            i += 1
            continue

        if state == "BLOCK_COMMENT":
            if ch == "*" and nxt == "/":
                state = "CODE"
                i += 2
                continue
            if ch == "\n":
                line += 1
            i += 1
            continue

        if state in ("SQ_STRING", "DQ_STRING"):
            quote = "'" if state == "SQ_STRING" else '"'
            if ch == "\\":
                if i + 1 < n and code[i + 1] == "\n":
                    line += 1
                i += 2
                continue
            if ch == "\n":
                errors.append(f"{label}: unclosed string literal at line {line}")
                state = "CODE"
                line += 1
                i += 1
                continue
            if ch == quote:
                state = "CODE"
                last_sig = "STRING"
            i += 1
            continue

        if state == "REGEX":
            if ch == "\\":
                if i + 1 >= n or code[i + 1] == "\n":
                    errors.append(f"{label}: trailing backslash in regex at line {line}")
                    state = "CODE"
                    i += 1
                    continue
                i += 2
                continue
            if ch == "\n":
                errors.append(f"{label}: unterminated regex literal at line {line}")
                state = "CODE"
                line += 1
                i += 1
                continue
            if ch == "[" and not regex_in_char_class:
                regex_in_char_class = True
            elif ch == "]" and regex_in_char_class:
                regex_in_char_class = False
            elif ch == "/" and not regex_in_char_class:
                state = "CODE"
                last_sig = "STRING"
                i += 1
                while i < n and code[i].isalpha():
                    i += 1
                continue
            i += 1
            continue

        if state == "TEMPLATE":
            if ch == "\\":
                if i + 1 < n and code[i + 1] == "\n":
                    line += 1
                i += 2
                continue
            if ch == "`":
                state = "CODE"
                last_sig = "STRING"
                i += 1
                continue
            if ch == "$" and nxt == "{":
                template_brace_depths.append(len(stack))
                stack.append(("{", line))
                state = "CODE"
                last_sig = "PUNCT"
                i += 2
                continue
            if ch == "\n":
                line += 1
            i += 1
            continue

        # state == "CODE"
        if ch in " \t\r":
            i += 1
            continue
        if ch == "\n":
            line += 1
            i += 1
            continue

        if ch == "/" and nxt == "/":
            state = "LINE_COMMENT"
            i += 2
            continue
        if ch == "/" and nxt == "*":
            state = "BLOCK_COMMENT"
            i += 2
            continue

        if ch == "'":
            state = "SQ_STRING"
            i += 1
            continue
        if ch == '"':
            state = "DQ_STRING"
            i += 1
            continue
        if ch == "`":
            state = "TEMPLATE"
            i += 1
            continue

        if ch == "/":
            if last_sig in ("", "PUNCT", "KW_EXPR"):
                state = "REGEX"
                regex_in_char_class = False
            else:
                last_sig = "PUNCT"
            i += 1
            continue

        if ch in "([{":
            stack.append((ch, line))
            last_sig = "PUNCT"
            i += 1
            continue

        if ch in ")]}":
            expected = {")": "(", "]": "[", "}": "{"}[ch]
            if not stack:
                errors.append(f"{label}: unmatched closing {ch!r} at line {line}")
            else:
                open_ch, open_line = stack.pop()
                if open_ch != expected:
                    errors.append(
                        f"{label}: mismatched {open_ch!r} (line {open_line}) closed by "
                        f"{ch!r} (line {line})"
                    )
                elif ch == "}" and template_brace_depths and len(stack) == template_brace_depths[-1]:
                    template_brace_depths.pop()
                    state = "TEMPLATE"
                    i += 1
                    continue
            last_sig = (
                "CLOSE_PAREN"
                if ch == ")"
                else ("CLOSE_BRACKET" if ch == "]" else "PUNCT")
            )
            i += 1
            continue

        if ch.isalpha() or ch in ("_", "$"):
            start = i
            while i < n and (code[i].isalnum() or code[i] in ("_", "$")):
                i += 1
            ident = code[start:i]
            last_sig = "KW_EXPR" if ident in _EXPR_KW else "IDENT"
            continue

        if ch.isdigit():
            while i < n and (code[i].isalnum() or code[i] in (".", "_")):
                i += 1
            last_sig = "NUMBER"
            continue

        last_sig = "PUNCT"
        i += 1

    if state in ("SQ_STRING", "DQ_STRING"):
        errors.append(f"{label}: unterminated string literal at EOF")
    elif state == "TEMPLATE":
        errors.append(f"{label}: unterminated template literal at EOF")
    elif state == "BLOCK_COMMENT":
        errors.append(f"{label}: unterminated /* ... */ block comment at EOF")
    elif state == "REGEX":
        errors.append(f"{label}: unterminated regex literal at EOF")

    for open_ch, open_line in stack:
        errors.append(f"{label}: unclosed {open_ch!r} opened at line {open_line}")

    if _BANNED_PLACEHOLDER_RE.search(code):
        errors.append(f"{label}: contains banned placeholder token (TODO/FIXME/XXX/TBD)")

    return errors


def _optional_node_check(code: str, label: str, wrap_nodered: bool = True) -> list[str]:
    node_bin = shutil.which("node")
    if not node_bin:
        return []
    body = (
        f"async function _nr(msg, flow, global, env, node, context) {{\n{code}\n}}"
        if wrap_nodered
        else code
    )
    with tempfile.NamedTemporaryFile("w", suffix=".js", encoding="utf-8", delete=True) as tf:
        tf.write(body)
        tf.flush()
        proc = subprocess.run(
            [node_bin, "--check", tf.name],
            check=False,
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            return [f"{label}: node --check failed: {proc.stderr.strip()}"]
    return []


def validate_nodered_flows(flows_path: Path) -> dict[str, Any]:
    """Validate ``node-red/flows.json`` 5-tab topology, wires, Modbus config, and embedded JS."""
    errors: list[str] = []
    if not flows_path.is_file():
        return {"errors": [f"Missing flows.json: {flows_path}"], "tabs": [], "function_nodes": []}

    try:
        nodes = json.loads(flows_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return {"errors": [f"Invalid JSON in {flows_path}: {exc}"], "tabs": [], "function_nodes": []}

    if not isinstance(nodes, list):
        return {"errors": ["flows.json root must be a JSON array"], "tabs": [], "function_nodes": []}

    node_ids: set[str] = set()
    tabs: list[dict[str, Any]] = []
    function_nodes: list[dict[str, Any]] = []
    modbus_clients: list[dict[str, Any]] = []
    template_nodes: list[dict[str, Any]] = []
    http_nodes: list[dict[str, Any]] = []
    ui_nodes: list[dict[str, Any]] = []
    js_chunks: list[str] = []

    for idx, node in enumerate(nodes):
        if not isinstance(node, dict):
            errors.append(f"Node #{idx} is not an object")
            continue
        nid = node.get("id")
        ntype = node.get("type", "")
        if not nid:
            errors.append(f"Node #{idx} missing 'id'")
        elif nid in node_ids:
            errors.append(f"Duplicate node id {nid!r}")
        else:
            node_ids.add(nid)

        if ntype == "tab":
            tabs.append(node)
        elif ntype == "function":
            function_nodes.append(node)
        elif ntype == "modbus-client":
            modbus_clients.append(node)
        elif ntype == "template":
            template_nodes.append(node)
        elif ntype == "http request":
            http_nodes.append(node)
        elif str(ntype).startswith("ui-") or str(ntype).startswith("ui_"):
            ui_nodes.append(node)

    tab_ids = {t.get("id") for t in tabs}
    if len(tabs) != 5:
        errors.append(f"Expected exactly 5 tabs in flows.json, found {len(tabs)}")
    else:
        folded_labels = [_fold_ascii(str(t.get("label", ""))) for t in tabs]
        expected_tab_roots = (
            ("aquisic", "Tab 1 Aquisição"),
            ("analis", "Tab 2 Análise"),
            ("atuac", "Tab 3 Atuação"),
            ("dashboard", "Tab 4 Dashboard"),
            ("test", "Tab 5 Testes/Sim"),
        )
        for root_kw, desc in expected_tab_roots:
            if not any(root_kw in lbl or ("sim" in lbl and root_kw == "test") for lbl in folded_labels):
                errors.append(f"Missing expected Node-RED tab matching {desc} ({folded_labels})")

    # Check orphan nodes and dangling wires
    for node in nodes:
        if not isinstance(node, dict):
            continue
        nid = node.get("id", "<unknown>")
        ntype = str(node.get("type", ""))
        z = node.get("z")
        if ntype != "tab" and z is not None and z != "" and z not in tab_ids:
            errors.append(f"Orphan node {nid!r} ({ntype}) references missing tab z={z!r}")
        wires = node.get("wires")
        if isinstance(wires, list):
            for branch in wires:
                if isinstance(branch, list):
                    for target_id in branch:
                        if target_id not in node_ids:
                            errors.append(
                                f"Dangling wire from {nid!r} ({ntype}) to missing {target_id!r}"
                            )

    for fn in function_nodes:
        fname = fn.get("name") or fn.get("id") or "<function>"
        for field_name in ("func", "initialize", "finalize"):
            code = fn.get(field_name)
            if code is None:
                continue
            if field_name == "func" and not str(code).strip():
                errors.append(f"Function node {fname!r} has empty 'func' code")
                continue
            if str(code).strip():
                js_chunks.append(str(code))
                lbl = f"function:{fname}.{field_name}"
                errors.extend(validate_js_syntax(str(code), label=lbl))
                errors.extend(_optional_node_check(str(code), label=lbl, wrap_nodered=True))

    return {
        "tabs": tabs,
        "function_nodes": function_nodes,
        "modbus_clients": modbus_clients,
        "template_nodes": template_nodes,
        "http_nodes": http_nodes,
        "ui_nodes": ui_nodes,
        "js_combined": "\n".join(js_chunks),
        "all_nodes": nodes,
        "errors": errors,
    }


def assert_nodered_flows_contract(flows_path: Path) -> dict[str, Any]:
    """Validate ``flows.json`` and raise ``AssertionError`` if any error is found."""
    summary = validate_nodered_flows(flows_path)
    if summary["errors"]:
        raise AssertionError("\n".join(summary["errors"]))
    return summary

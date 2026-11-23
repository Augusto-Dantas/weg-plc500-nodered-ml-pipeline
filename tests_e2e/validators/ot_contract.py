"""Pure-Python IEC 61131-3 Structured Text & Modbus CSV contract validators."""

from __future__ import annotations

import csv
import io
import re
from pathlib import Path

from . import ValidationResult

# Canonical 23-entry 0-based Modbus TCP contract
# (area, address, accepted_names, expected_type)
CANONICAL_MODBUS_MAP: tuple[tuple[str, int, tuple[str, ...], str], ...] = (
    ("DI", 0, ("xDI_Presenca",), "BOOL"),
    ("DI", 1, ("xDI_NivelAlto",), "BOOL"),
    ("DI", 2, ("xDI_Sobretemp",), "BOOL"),
    ("DI", 3, ("xDI_EmergenciaOK",), "BOOL"),
    ("DI", 4, ("xFlag_EmergTravada",), "BOOL"),
    ("DI", 5, ("xFlag_WatchdogAtivo",), "BOOL"),
    ("COIL", 0, ("xCmd_Motor",), "BOOL"),
    ("COIL", 1, ("xCmd_Valvula",), "BOOL"),
    ("COIL", 2, ("xCmd_Alarme",), "BOOL"),
    ("COIL", 3, ("xCmd_ResetFalha",), "BOOL"),
    ("COIL", 4, ("xCmd_HabRemoto",), "BOOL"),
    ("IR", 0, ("uiCnt_Presenca",), "UINT"),
    ("IR", 1, ("uiCnt_NivelAlto",), "UINT"),
    ("IR", 2, ("uiCnt_Sobretemp",), "UINT"),
    ("IR", 3, ("uiCnt_Emergencia",), "UINT"),
    ("IR", 4, ("uiEstado_CLP",), "UINT"),
    ("IR", 5, ("wMascara_DO_Real",), "WORD"),
    ("IR", 6, ("uiEcho_Heartbeat",), "UINT"),
    ("IR", 7, ("uiCod_Intertrav",), "UINT"),
    ("HR", 0, ("uiHeartbeat_Sup", "uiHeartbeat_NR", "uiHeartbeat_N8N"), "UINT"),
    ("HR", 1, ("uiTimeout_WD_ms",), "UINT"),
    ("HR", 2, ("uiSeveridade_IA",), "UINT"),
    ("HR", 3, ("uiVersao_Mapa",), "UINT"),
)


def strip_st_comments(text: str) -> str:
    """Remove IEC 61131-3 ``(* ... *)`` and ``// ...`` comments while preserving newlines."""
    out: list[str] = []
    i = 0
    n = len(text)
    in_sq = False
    in_dq = False
    block_depth = 0
    in_line = False

    while i < n:
        ch = text[i]
        nxt = text[i + 1] if i + 1 < n else ""

        if in_line:
            if ch == "\n":
                in_line = False
                out.append("\n")
            else:
                out.append(" ")
            i += 1
            continue

        if block_depth > 0:
            if ch == "(" and nxt == "*":
                block_depth += 1
                out.extend((" ", " "))
                i += 2
                continue
            if ch == "*" and nxt == ")":
                block_depth -= 1
                out.extend((" ", " "))
                i += 2
                continue
            out.append("\n" if ch == "\n" else " ")
            i += 1
            continue

        if in_sq:
            out.append(ch)
            if ch == "$" and i + 1 < n:
                out.append(nxt)
                i += 2
                continue
            if ch == "'":
                in_sq = False
            i += 1
            continue

        if in_dq:
            out.append(ch)
            if ch == "$" and i + 1 < n:
                out.append(nxt)
                i += 2
                continue
            if ch == '"':
                in_dq = False
            i += 1
            continue

        if ch == "(" and nxt == "*":
            block_depth = 1
            out.extend((" ", " "))
            i += 2
            continue
        if ch == "/" and nxt == "/":
            in_line = True
            out.extend((" ", " "))
            i += 2
            continue
        if ch == "'":
            in_sq = True
        elif ch == '"':
            in_dq = True

        out.append(ch)
        i += 1

    return "".join(out)


def parse_st_declarations(st_text: str) -> dict[str, tuple[str, str | None]]:
    """Extract ``name -> (IEC_TYPE, init_expr)`` declarations from ST ``VAR*`` blocks."""
    cleaned = strip_st_comments(st_text)
    decls: dict[str, tuple[str, str | None]] = {}
    var_block_re = re.compile(
        r"\bVAR(?:_GLOBAL|_INPUT|_OUTPUT|_IN_OUT)?(?:\s+(?:CONSTANT|RETAIN|PERSISTENT))*\b"
        r"(.*?)\bEND_VAR\b",
        re.IGNORECASE | re.DOTALL,
    )
    decl_re = re.compile(
        r"^\s*([A-Za-z_][A-Za-z0-9_]*(?:\s*,\s*[A-Za-z_][A-Za-z0-9_]*)*)"
        r"(?:\s+AT\s+%[IQM][XBWD]?\d+(?:\.\d+)?)?\s*:\s*"
        r"([A-Za-z_][A-Za-z0-9_]*(?:\s*\([^)]*\))?)"
        r"(?:\s*:=\s*(.+?))?\s*$",
        re.IGNORECASE | re.DOTALL,
    )

    for block in var_block_re.finditer(cleaned):
        body = block.group(1)
        for stmt in body.split(";"):
            stmt_s = stmt.strip()
            if not stmt_s:
                continue
            match = decl_re.match(stmt_s)
            if not match:
                continue
            names_raw, dtype_raw, init_raw = match.groups()
            dtype = re.sub(r"\s+", " ", dtype_raw.strip()).upper()
            init_val = init_raw.strip() if init_raw else None
            for name in names_raw.split(","):
                vname = name.strip()
                if vname:
                    decls[vname] = (dtype, init_val)
    return decls


def validate_debounce_counter(fb_deb_text: str) -> ValidationResult:
    """Validate ``FB_DebounceCounter.st`` TON 50 ms, R_TRIG, and wrap-16 UINT semantics."""
    cleaned = strip_st_comments(fb_deb_text)
    errors: list[str] = []

    if not re.search(r"\bTON\b", cleaned, re.IGNORECASE):
        errors.append("FB_DebounceCounter missing TON timer")
    if not re.search(r"(?:T#|TIME#)50MS\b", cleaned, re.IGNORECASE):
        errors.append("FB_DebounceCounter missing T#50MS debounce preset")
    if not re.search(r"\bR_TRIG\b", cleaned, re.IGNORECASE):
        errors.append("FB_DebounceCounter missing R_TRIG rising-edge detector")
    if not re.search(r"\bUINT\b", cleaned, re.IGNORECASE):
        errors.append("FB_DebounceCounter missing UINT counter type")
    if not re.search(r"\+\s*1\b", cleaned):
        errors.append("FB_DebounceCounter missing '+ 1' modular counter increment")
    if re.search(r"<\s*65535\b|\bMIN\s*\([^;]*65535", cleaned, re.IGNORECASE):
        errors.append("FB_DebounceCounter must not clamp UINT counter at 65535")

    return ValidationResult(ok=not errors, errors=errors)


def validate_watchdog_order(fb_wd_text: str) -> ValidationResult:
    """Verify ``tonWatchdog`` evaluates BEFORE ``uiLastHeartbeat`` update and bounds [2000..30000]."""
    cleaned = strip_st_comments(fb_wd_text)
    errors: list[str] = []

    ton_match = re.search(r"\btonWatchdog\s*\(", cleaned, re.IGNORECASE)
    upd_match = re.search(r"\buiLastHeartbeat\s*:=", cleaned, re.IGNORECASE)
    if ton_match is None:
        errors.append("Missing tonWatchdog(...) invocation")
    if upd_match is None:
        errors.append("Missing uiLastHeartbeat := assignment")
    if ton_match is not None and upd_match is not None and ton_match.start() >= upd_match.start():
        errors.append(
            "Ordering bug: tonWatchdog(...) MUST execute strictly BEFORE uiLastHeartbeat :="
        )

    if not re.search(r">=\s*2000\b", cleaned):
        errors.append("Missing >= 2000 lower timeout bound")
    if not re.search(r"<=\s*30000\b", cleaned):
        errors.append("Missing <= 30000 upper timeout bound")
    if not re.search(r"(?:T#|TIME#)5S\b|(?:T#|TIME#)5000MS\b|\b5000\b", cleaned, re.IGNORECASE):
        errors.append("Missing 5000 ms / T#5S fallback watchdog timeout")
    if "uiEcho_Heartbeat" not in cleaned:
        errors.append("Missing uiEcho_Heartbeat assignment")
    if "xFlag_WatchdogAtivo" not in cleaned:
        errors.append("Missing xFlag_WatchdogAtivo output assignment")

    return ValidationResult(ok=not errors, errors=errors)


def _has_unconditional_assignment(cleaned_st: str, var_name: str, value_regex: str) -> bool:
    """Return True if ``var_name := value_regex`` executes at IF-nesting depth 0."""
    tokens = re.finditer(
        r"\bIF\b|\bEND_IF\b|\bCASE\b|\bEND_CASE\b|"
        + rf"\b{re.escape(var_name)}\s*:=\s*{value_regex}\s*;",
        cleaned_st,
        re.IGNORECASE,
    )
    depth = 0
    for tok in tokens:
        word = tok.group(0).strip().upper()
        if word in ("IF", "CASE"):
            depth += 1
        elif word in ("END_IF", "END_CASE"):
            depth = max(0, depth - 1)
        elif depth == 0:
            return True
    return False


def validate_safety_interlock(fb_si_text: str, plc_prg_text: str) -> ValidationResult:
    """Validate ``FB_SafetyInterlock.st`` & ``PLC_PRG.st`` precedence, reset guard & DI3 NF."""
    si_clean = strip_st_comments(fb_si_text)
    prg_clean = strip_st_comments(plc_prg_text)
    errors: list[str] = []

    # 1. DI3 (%IX0.3) direct NF read without debounce in PLC_PRG
    if not re.search(r"xDI_EmergenciaOK\s*:=\s*%IX0\.3\b", prg_clean, re.IGNORECASE):
        errors.append("PLC_PRG must assign xDI_EmergenciaOK := %IX0.3 directly without debounce")
    if re.search(r"(?:TON|FB_DebounceCounter)[^;]*%IX0\.3", prg_clean, re.IGNORECASE):
        errors.append("PLC_PRG must NOT pass %IX0.3 through TON or FB_DebounceCounter")

    # 2. Emergency latch & reset guard
    if not re.search(r"NOT\s+xDI_EmergenciaOK", si_clean, re.IGNORECASE):
        errors.append("FB_SafetyInterlock missing 'NOT xDI_EmergenciaOK' fail-safe check")

    reset_branch = re.search(
        r"ELSIF\b(.*?)\bTHEN\b[^;]*?xFlag_EmergTravada\s*:=\s*FALSE",
        si_clean,
        re.IGNORECASE | re.DOTALL,
    )
    if reset_branch is None:
        errors.append("FB_SafetyInterlock missing ELSIF reset branch clearing xFlag_EmergTravada")
    else:
        cond = reset_branch.group(1)
        for req in (
            r"\bxCmd_ResetFalha\b",
            r"\bxDI_EmergenciaOK\b",
            r"\bNOT\s*\(?\s*xDI_Sobretemp\b",
        ):
            if not re.search(req, cond, re.IGNORECASE):
                errors.append(f"Emergency reset condition missing guard matching {req}")

    # 3. Main FSM IF / ELSIF / ELSE precedence
    p_emerg = re.search(r"\bIF\s+(?:\(?\s*)?xFlag_EmergTravada\b", si_clean, re.IGNORECASE)
    p_sobre = re.search(r"\bELSIF\s+(?:\(?\s*)?xDI_Sobretemp\b", si_clean, re.IGNORECASE)
    p_wd = re.search(r"\bELSIF\s+(?:\(?\s*)?xFlag_WatchdogAtivo\b", si_clean, re.IGNORECASE)
    p_rem = re.search(r"\bELSIF\s+(?:\(?\s*)?xCmd_HabRemoto\b", si_clean, re.IGNORECASE)

    branches = [
        ("IF xFlag_EmergTravada", p_emerg),
        ("ELSIF xDI_Sobretemp", p_sobre),
        ("ELSIF xFlag_WatchdogAtivo", p_wd),
        ("ELSIF xCmd_HabRemoto", p_rem),
    ]
    missing = [name for name, m in branches if m is None]
    if missing:
        errors.append(f"FB_SafetyInterlock missing FSM branches: {', '.join(missing)}")
    else:
        positions = [m.start() for _, m in branches if m is not None]
        if positions != sorted(positions):
            errors.append(
                "FB_SafetyInterlock FSM precedence out of order "
                "(must be EmergTravada > Sobretemp > Watchdog > HabRemoto)"
            )
        else:
            # Find matching ELSE after p_rem
            else_m = re.search(r"\bELSE\b", si_clean[p_rem.end() :], re.IGNORECASE)
            if else_m is None:
                errors.append("FB_SafetyInterlock FSM missing ELSE (INIT) branch")
            else:
                else_pos = p_rem.end() + else_m.start()
                end_if_m = re.search(r"\bEND_IF\b", si_clean[else_pos:], re.IGNORECASE)
                end_pos = else_pos + end_if_m.start() if end_if_m else len(si_clean)

                slices = [
                    ("EmergTravada", si_clean[p_emerg.start() : p_sobre.start()], "4", "1"),
                    ("Sobretemp", si_clean[p_sobre.start() : p_wd.start()], "2", "2"),
                    ("Watchdog", si_clean[p_wd.start() : p_rem.start()], "3", "3"),
                    ("HabRemoto", si_clean[p_rem.start() : else_pos], None, "0"),
                    ("INIT", si_clean[else_pos:end_pos], "0", "0"),
                ]
                for bname, bslice, exp_state, exp_cod in slices:
                    if exp_state is not None and not re.search(
                        rf"\buiEstado_CLP\s*:=\s*{exp_state}\b", bslice, re.IGNORECASE
                    ):
                        errors.append(f"Branch {bname} missing uiEstado_CLP := {exp_state}")
                    if not re.search(
                        rf"\buiCod_Intertrav\s*:=\s*{exp_cod}\b", bslice, re.IGNORECASE
                    ):
                        errors.append(f"Branch {bname} missing uiCod_Intertrav := {exp_cod}")

                sobre_slice = slices[1][1]
                if not re.search(r"NOT\s+xFlag_WatchdogAtivo", sobre_slice, re.IGNORECASE):
                    errors.append(
                        "Sobretemp branch DO3 must depend on NOT xFlag_WatchdogAtivo"
                    )

    # 4. Unconditional uiVersao_Mapa := 100
    combined_uncond = _has_unconditional_assignment(
        si_clean, "uiVersao_Mapa", "100"
    ) or _has_unconditional_assignment(prg_clean, "uiVersao_Mapa", "100")
    if not combined_uncond:
        errors.append("uiVersao_Mapa := 100 must execute unconditionally every scan")

    return ValidationResult(ok=not errors, errors=errors)


def _normalize_area(raw: str) -> str:
    val = raw.strip().lower()
    if "discrete" in val or val in ("di", "fc02", "entradas discretas"):
        return "DI"
    if "coil" in val or "bobina" in val or val in ("do", "fc01", "fc05", "fc15"):
        return "COIL"
    if "input register" in val or "input_register" in val or val in ("ir", "fc04"):
        return "IR"
    if "holding" in val or val in ("hr", "fc03", "fc06", "fc16"):
        return "HR"
    return raw.strip().upper()


def _pick_field(row: dict[str, str], candidates: tuple[str, ...]) -> str:
    lower_map = {k.strip().lower(): (v or "").strip() for k, v in row.items() if k is not None}
    for cand in candidates:
        if lower_map.get(cand):
            return lower_map[cand]
    for key, val in lower_map.items():
        if any(cand in key for cand in candidates) and val:
            return val
    return ""


def validate_st_csv_contract(gvl_text: str, csv_input: Path | str) -> ValidationResult:
    """Cross-validate ``modbus_mapping.csv`` against ``GVL_Modbus.st`` and the 23-item contract."""
    errors: list[str] = []
    decls = parse_st_declarations(gvl_text)
    if not decls:
        errors.append("No variable declarations parsed from GVL_Modbus.st")

    if isinstance(csv_input, Path):
        if not csv_input.is_file():
            return ValidationResult(ok=False, errors=[f"Missing CSV file: {csv_input}"])
        csv_text = csv_input.read_text(encoding="utf-8")
    else:
        csv_text = csv_input

    reader = csv.DictReader(io.StringIO(csv_text))
    if not reader.fieldnames:
        return ValidationResult(ok=False, errors=["modbus_mapping.csv has no header"])

    seen: dict[tuple[str, int], tuple[str, str]] = {}
    for idx, row in enumerate(reader, start=2):
        area_raw = _pick_field(
            row, ("area", "modbus_area", "register_type", "tipo_modbus", "table")
        )
        addr_raw = _pick_field(row, ("address", "addr", "offset", "endereco", "index"))
        var_name = _pick_field(
            row, ("variable", "variable_name", "symbol", "variavel", "name")
        )
        dtype_raw = _pick_field(row, ("type", "data_type", "iec_type", "tipo"))

        if not area_raw or not addr_raw or not var_name:
            errors.append(f"CSV row {idx} missing area/address/variable: {row}")
            continue

        area = _normalize_area(area_raw)
        try:
            addr = int(addr_raw, 0)
        except ValueError:
            errors.append(f"CSV row {idx} invalid integer address {addr_raw!r}")
            continue

        key = (area, addr)
        if key in seen:
            errors.append(f"Duplicate Modbus mapping for {area}[{addr}]")
            continue
        dtype = dtype_raw.upper()
        seen[key] = (var_name, dtype)

    for area, addr, accepted_names, exp_type in CANONICAL_MODBUS_MAP:
        key = (area, addr)
        if key not in seen:
            errors.append(f"Missing canonical Modbus entry {area}[{addr}] ({accepted_names[0]})")
            continue
        var_name, csv_type = seen[key]
        if var_name not in accepted_names:
            errors.append(
                f"{area}[{addr}] variable {var_name!r} not in {accepted_names}"
            )
        if csv_type and csv_type != exp_type:
            errors.append(f"{area}[{addr}] CSV type {csv_type!r} != expected {exp_type!r}")
        if var_name not in decls:
            errors.append(f"CSV variable {var_name!r} not declared in GVL_Modbus.st")
        else:
            st_type, _ = decls[var_name]
            if st_type != exp_type:
                errors.append(
                    f"GVL_Modbus.st {var_name!r} type {st_type!r} != expected {exp_type!r}"
                )

    for area, addr in seen:
        if not any(a == area and i == addr for a, i, _, _ in CANONICAL_MODBUS_MAP):
            errors.append(f"Unexpected/out-of-range Modbus entry {area}[{addr}]")

    return ValidationResult(
        ok=not errors,
        errors=errors,
        metadata={"csv_entries": len(seen), "gvl_decls": len(decls)},
    )

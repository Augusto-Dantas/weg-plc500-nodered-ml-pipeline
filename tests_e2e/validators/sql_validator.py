"""Pure-Python PostgreSQL DDL lexer/parser and PLC500 schema contract validator."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from . import ValidationResult


@dataclass
class SqlColumn:
    """Parsed SQL column definition."""

    name: str
    data_type: str
    raw_def: str
    not_null: bool = False
    primary_key: bool = False
    references: tuple[str, str] | None = None
    checks: list[str] = field(default_factory=list)


@dataclass
class SqlTable:
    """Parsed SQL table definition."""

    name: str
    columns: dict[str, SqlColumn] = field(default_factory=dict)
    table_constraints: list[str] = field(default_factory=list)


@dataclass
class SqlIndex:
    """Parsed SQL index definition."""

    name: str
    table: str
    columns_expr: str
    unique: bool = False


@dataclass
class SqlView:
    """Parsed SQL view definition."""

    name: str
    query: str


@dataclass
class SqlValidationResult:
    """Structured result from parsing PostgreSQL DDL."""

    tables: dict[str, SqlTable] = field(default_factory=dict)
    indexes: dict[str, SqlIndex] = field(default_factory=dict)
    views: dict[str, SqlView] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)


def strip_comments_and_balance(sql_text: str) -> tuple[str, list[str]]:
    """Strip SQL comments while preserving string/dollar-quoted literals and check balance."""
    out: list[str] = []
    errors: list[str] = []
    i = 0
    n = len(sql_text)
    paren_depth = 0
    in_sq = False
    in_line = False
    in_block = False
    dollar_tag: str | None = None

    while i < n:
        ch = sql_text[i]
        nxt = sql_text[i + 1] if i + 1 < n else ""

        if in_line:
            if ch == "\n":
                in_line = False
                out.append("\n")
            else:
                out.append(" ")
            i += 1
            continue

        if in_block:
            if ch == "*" and nxt == "/":
                in_block = False
                out.extend((" ", " "))
                i += 2
                continue
            out.append("\n" if ch == "\n" else " ")
            i += 1
            continue

        if dollar_tag is not None:
            if sql_text.startswith(dollar_tag, i):
                out.append(dollar_tag)
                i += len(dollar_tag)
                dollar_tag = None
                continue
            out.append(ch)
            i += 1
            continue

        if in_sq:
            out.append(ch)
            if ch == "'" and nxt == "'":
                out.append("'")
                i += 2
                continue
            if ch == "'":
                in_sq = False
            i += 1
            continue

        if ch == "-" and nxt == "-":
            in_line = True
            out.extend((" ", " "))
            i += 2
            continue
        if ch == "/" and nxt == "*":
            in_block = True
            out.extend((" ", " "))
            i += 2
            continue
        if ch == "'":
            in_sq = True
            out.append(ch)
            i += 1
            continue
        if ch == "$":
            tag_m = re.match(r"\$[A-Za-z_0-9]*\$", sql_text[i:])
            if tag_m:
                dollar_tag = tag_m.group(0)
                out.append(dollar_tag)
                i += len(dollar_tag)
                continue

        if ch == "(":
            paren_depth += 1
        elif ch == ")":
            paren_depth -= 1
            if paren_depth < 0:
                errors.append(f"Unexpected ')' at offset {i}")
                paren_depth = 0

        out.append(ch)
        i += 1

    if in_sq:
        errors.append("Unterminated single-quoted SQL string literal")
    if in_block:
        errors.append("Unterminated /* ... */ SQL block comment")
    if dollar_tag is not None:
        errors.append(f"Unterminated dollar-quoted block {dollar_tag}")
    if paren_depth > 0:
        errors.append(f"Unclosed '(' in SQL ({paren_depth} unclosed)")

    return "".join(out), errors


def _split_top_level(text: str, sep: str) -> list[str]:
    parts: list[str] = []
    buf: list[str] = []
    depth = 0
    in_sq = False
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if in_sq:
            buf.append(ch)
            if ch == "'" and i + 1 < n and text[i + 1] == "'":
                buf.append("'")
                i += 2
                continue
            if ch == "'":
                in_sq = False
            i += 1
            continue
        if ch == "'":
            in_sq = True
            buf.append(ch)
            i += 1
            continue
        if ch == "(":
            depth += 1
        elif ch == ")" and depth > 0:
            depth -= 1
        elif ch == sep and depth == 0:
            parts.append("".join(buf).strip())
            buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1
    tail = "".join(buf).strip()
    if tail:
        parts.append(tail)
    return parts


def _clean_ident(name: str) -> str:
    part = name.strip().split(".")[-1]
    return part.strip('"').lower()


def validate_postgres_ddl(sql_text: str) -> SqlValidationResult:
    """Parse PostgreSQL DDL statements into tables, indexes, views, and lexical errors."""
    cleaned, errors = strip_comments_and_balance(sql_text)
    res = SqlValidationResult(errors=list(errors))
    stmts = _split_top_level(cleaned, ";")

    create_table_re = re.compile(
        r"^CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?([A-Za-z0-9_.\"]+)\s*\((.*)\)\s*$",
        re.IGNORECASE | re.DOTALL,
    )
    create_index_re = re.compile(
        r"^CREATE\s+(UNIQUE\s+)?INDEX\s+(?:CONCURRENTLY\s+)?(?:IF\s+NOT\s+EXISTS\s+)?"
        r"([A-Za-z0-9_.\"]+)\s+ON\s+(?:ONLY\s+)?([A-Za-z0-9_.\"]+)\s*"
        r"(?:USING\s+[A-Za-z0-9_]+\s*)?\((.+)\)\s*(?:WHERE\s+.*)?$",
        re.IGNORECASE | re.DOTALL,
    )
    create_view_re = re.compile(
        r"^CREATE\s+(?:OR\s+REPLACE\s+)?(?:MATERIALIZED\s+)?VIEW\s+"
        r"([A-Za-z0-9_.\"]+)\s+AS\s+(.+)$",
        re.IGNORECASE | re.DOTALL,
    )

    for stmt in stmts:
        if not stmt:
            continue
        if re.search(r"\bDROP\s+DATABASE\b|\bTRUNCATE\b", stmt, re.IGNORECASE):
            res.errors.append(f"Forbidden destructive SQL statement: {stmt[:60]}")
            continue

        tbl_m = create_table_re.match(stmt)
        if tbl_m:
            tname = _clean_ident(tbl_m.group(1))
            body = tbl_m.group(2)
            tbl = SqlTable(name=tname)
            for item in _split_top_level(body, ","):
                if not item:
                    continue
                if re.match(
                    r"^(?:CONSTRAINT|PRIMARY\s+KEY|FOREIGN\s+KEY|UNIQUE|CHECK)\b",
                    item,
                    re.IGNORECASE,
                ):
                    tbl.table_constraints.append(item)
                    fk_m = re.search(
                        r"FOREIGN\s+KEY\s*\(\s*([A-Za-z0-9_\"]+)\s*\)\s*"
                        r"REFERENCES\s+([A-Za-z0-9_.\"]+)\s*\(\s*([A-Za-z0-9_\"]+)\s*\)",
                        item,
                        re.IGNORECASE,
                    )
                    if fk_m:
                        col_k = _clean_ident(fk_m.group(1))
                        if col_k in tbl.columns:
                            tbl.columns[col_k].references = (
                                _clean_ident(fk_m.group(2)),
                                _clean_ident(fk_m.group(3)),
                            )
                    continue

                col_m = re.match(
                    r'^([A-Za-z0-9_"]+)\s+([A-Za-z0-9_]+(?:\s+[A-Za-z0-9_]+)?(?:\s*\([^)]*\))?)',
                    item,
                    re.IGNORECASE,
                )
                if not col_m:
                    continue
                cname = _clean_ident(col_m.group(1))
                dtype = re.sub(r"\s+", " ", col_m.group(2).strip()).upper()
                ref_m = re.search(
                    r"\bREFERENCES\s+([A-Za-z0-9_.\"]+)\s*\(\s*([A-Za-z0-9_\"]+)\s*\)",
                    item,
                    re.IGNORECASE,
                )
                refs = (
                    (_clean_ident(ref_m.group(1)), _clean_ident(ref_m.group(2)))
                    if ref_m
                    else None
                )
                checks = [
                    cm.group(1).strip()
                    for cm in re.finditer(
                        r"\bCHECK\s*\((.+)\)", item, re.IGNORECASE | re.DOTALL
                    )
                ]
                tbl.columns[cname] = SqlColumn(
                    name=cname,
                    data_type=dtype,
                    raw_def=item,
                    not_null=bool(re.search(r"\bNOT\s+NULL\b", item, re.IGNORECASE)),
                    primary_key=bool(re.search(r"\bPRIMARY\s+KEY\b", item, re.IGNORECASE)),
                    references=refs,
                    checks=checks,
                )
            res.tables[tname] = tbl
            continue

        idx_m = create_index_re.match(stmt)
        if idx_m:
            iname = _clean_ident(idx_m.group(2))
            res.indexes[iname] = SqlIndex(
                name=iname,
                table=_clean_ident(idx_m.group(3)),
                columns_expr=idx_m.group(4).strip(),
                unique=bool(idx_m.group(1)),
            )
            continue

        view_m = create_view_re.match(stmt)
        if view_m:
            vname = _clean_ident(view_m.group(1))
            res.views[vname] = SqlView(name=vname, query=view_m.group(2).strip())

    return res


def assert_plc500_sql_contract(sql_path: Path) -> ValidationResult:
    """Validate ``node-red/sql/init.sql`` against the PLC500 PostgreSQL schema contract."""
    if not sql_path.is_file():
        return ValidationResult(ok=False, errors=[f"Missing SQL DDL: {sql_path}"])

    parsed = validate_postgres_ddl(sql_path.read_text(encoding="utf-8"))
    errors = list(parsed.errors)

    required_tables = (
        "telemetria_sensores",
        "janelas_analiticas",
        "diagnosticos_ia",
        "comandos_atuadores",
    )
    for tname in required_tables:
        if tname not in parsed.tables:
            errors.append(f"Missing required table {tname!r}")

    tel = parsed.tables.get("telemetria_sensores")
    if tel is not None:
        if "cnt_emergencia" not in tel.columns:
            errors.append("telemetria_sensores missing 'cnt_emergencia' column")
        else:
            dt = tel.columns["cnt_emergencia"].data_type
            if not any(tok in dt for tok in ("INT", "SERIAL", "SMALLINT", "BIGINT")):
                errors.append(f"telemetria_sensores.cnt_emergencia non-integer type {dt!r}")
        if "capturado_em" not in tel.columns:
            errors.append("telemetria_sensores missing 'capturado_em' column")

    jan = parsed.tables.get("janelas_analiticas")
    if jan is not None:
        sev_col = jan.columns.get("severidade_estatistica")
        combined_checks = " ".join(
            (sev_col.checks if sev_col else []) + jan.table_constraints
        )
        if sev_col is None:
            errors.append("janelas_analiticas missing 'severidade_estatistica' column")
        elif not re.search(
            r"BETWEEN\s+0\s+AND\s+3|>=\s*0.*<=\s*3|IN\s*\(\s*0\s*,\s*1\s*,\s*2\s*,\s*3\s*\)",
            combined_checks,
            re.IGNORECASE | re.DOTALL,
        ):
            errors.append(
                "janelas_analiticas.severidade_estatistica missing CHECK enforcing 0..3"
            )

    diag = parsed.tables.get("diagnosticos_ia")
    if diag is not None:
        j_col = diag.columns.get("janela_id")
        if j_col is None or j_col.references != ("janelas_analiticas", "id"):
            errors.append(
                "diagnosticos_ia.janela_id must reference janelas_analiticas(id)"
            )
        orig_col = diag.columns.get("origem_decisao")
        combined_orig = " ".join(
            (orig_col.checks if orig_col else []) + diag.table_constraints
        )
        if orig_col is None:
            errors.append("diagnosticos_ia missing 'origem_decisao' column")
        elif "'LLM'" not in combined_orig or "'FALLBACK_STATS'" not in combined_orig:
            errors.append(
                "diagnosticos_ia.origem_decisao CHECK must allow 'LLM' and 'FALLBACK_STATS'"
            )

    cmd = parsed.tables.get("comandos_atuadores")
    if cmd is not None:
        d_col = cmd.columns.get("diagnostico_id")
        if d_col is None or d_col.references != ("diagnosticos_ia", "id"):
            errors.append(
                "comandos_atuadores.diagnostico_id must reference diagnosticos_ia(id)"
            )

    idx = parsed.indexes.get("idx_tel_capturado")
    if idx is None:
        errors.append("Missing required index 'idx_tel_capturado'")
    else:
        if idx.table != "telemetria_sensores":
            errors.append(
                f"idx_tel_capturado expected on telemetria_sensores, got {idx.table!r}"
            )
        if not re.search(r"\bcapturado_em\s+DESC\b", idx.columns_expr, re.IGNORECASE):
            errors.append(
                f"idx_tel_capturado must index (capturado_em DESC), got ({idx.columns_expr})"
            )

    if not parsed.views:
        errors.append("init.sql must define at least 1 operational SQL VIEW")
    else:
        for vname, view in parsed.views.items():
            if not any(tbl in view.query.lower() for tbl in parsed.tables):
                errors.append(
                    f"View {vname!r} does not reference any declared base table"
                )

    return ValidationResult(
        ok=not errors,
        errors=errors,
        metadata={
            "tables": sorted(parsed.tables),
            "indexes": sorted(parsed.indexes),
            "views": sorted(parsed.views),
            "parsed": parsed,
        },
    )

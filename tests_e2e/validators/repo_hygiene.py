"""Pure-Python Git repository hygiene, secret scan, and Overleaf parity validators."""

from __future__ import annotations

import hashlib
import re
import subprocess
import zipfile
from pathlib import Path

from . import ValidationResult

_SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("private_key", re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----")),
    ("openai_key", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b")),
    ("github_pat", re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b")),
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("slack_token", re.compile(r"\bxox[baprs]-[0-9A-Za-z-]{10,}\b")),
    ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("telegram_bot_token", re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{35}\b")),
)

_FORBIDDEN_TRACKED_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("GEMINI.md", re.compile(r"(?:^|/)GEMINI\.md$")),
    (".venv", re.compile(r"(?:^|/)\.venv/")),
    ("venv", re.compile(r"(?:^|/)venv/")),
    (".agents", re.compile(r"(?:^|/)\.agents/")),
    (".env", re.compile(r"(?:^|/)\.env(?:\.(?!example$).*)?$")),
    ("flows_cred.json", re.compile(r"(?:^|/)flows_cred\.json$")),
    ("key_or_cert", re.compile(r"\.(?:key|pem|p12|pfx)$")),
)

_SENSITIVE_ENV_KEYS = (
    "POSTGRES_PASSWORD",
    "NR_ADMIN_PASSWORD",
    "NR_CREDENTIAL_SECRET",
    "OPENAI_API_KEY",
    "TELEGRAM_BOT_TOKEN",
)


def git_tracked_files(repo_root: Path) -> list[str]:
    """Return all tracked paths in ``repo_root`` via ``git ls-files``."""
    proc = subprocess.run(
        ["git", "-C", str(repo_root), "ls-files", "-z"],
        check=True,
        capture_output=True,
    )
    raw = proc.stdout.decode("utf-8", errors="replace")
    return [entry for entry in raw.split("\x00") if entry]


def git_check_ignore(repo_root: Path, candidates: list[str]) -> dict[str, bool]:
    """Return mapping ``candidate -> is_ignored`` via ``git check-ignore``."""
    if not candidates:
        return {}
    payload = ("\x00".join(candidates) + "\x00").encode("utf-8")
    proc = subprocess.run(
        ["git", "-C", str(repo_root), "check-ignore", "--stdin", "-z"],
        check=False,
        input=payload,
        capture_output=True,
    )
    if proc.returncode not in (0, 1):
        stderr = proc.stderr.decode("utf-8", errors="replace")
        raise RuntimeError(f"git check-ignore failed ({proc.returncode}): {stderr}")
    ignored = {
        entry
        for entry in proc.stdout.decode("utf-8", errors="replace").split("\x00")
        if entry
    }
    return {candidate: (candidate in ignored) for candidate in candidates}


def scan_text_for_secrets(text: str, filename: str = "<input>") -> list[str]:
    """Scan ``text`` for hardcoded secret tokens or private keys."""
    findings: list[str] = []
    for label, pattern in _SECRET_PATTERNS:
        for match in pattern.finditer(text):
            findings.append(f"{filename}: detected {label} pattern ({match.group(0)[:12]}...)")
    return findings


def check_env_example_placeholders(env_text: str) -> list[str]:
    """Verify sensitive keys in ``.env.example`` only contain placeholders or empty values."""
    errors: list[str] = []
    kv: dict[str, str] = {}
    for line in env_text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, val = stripped.split("=", 1)
        kv[key.strip()] = val.strip()

    for key in _SENSITIVE_ENV_KEYS:
        if key not in kv:
            errors.append(f".env.example missing expected key {key}")
            continue
        val = kv[key].strip("\"'")
        if val == "":
            continue
        upper = val.upper()
        if not (
            upper.startswith("CHANGE_ME")
            or "PLACEHOLDER" in upper
            or "EXAMPLE" in upper
        ):
            errors.append(f".env.example key {key} appears to hold a non-placeholder value")
    return errors


def check_forbidden_tracked_paths(tracked_paths: list[str]) -> ValidationResult:
    """Verify no secret, local metadata, or virtualenv path is in ``tracked_paths``."""
    errors: list[str] = []
    for path in tracked_paths:
        for label, pattern in _FORBIDDEN_TRACKED_PATTERNS:
            if pattern.search(path):
                errors.append(f"Forbidden tracked path ({label}): {path}")
        if "credentials" in path.lower() and "example" not in path.lower():
            errors.append(f"Forbidden tracked credentials path: {path}")
    return ValidationResult(
        ok=not errors,
        errors=errors,
        metadata={"checked_count": len(tracked_paths)},
    )


def verify_overleaf_zip_parity(zip_path: Path, relatorio_dir: Path) -> ValidationResult:
    """Compare SHA-256 of every file inside ``zip_path`` against ``relatorio_dir``."""
    errors: list[str] = []
    matched: list[str] = []
    if not zip_path.is_file():
        return ValidationResult(ok=False, errors=[f"Missing Overleaf ZIP: {zip_path}"])

    with zipfile.ZipFile(zip_path, "r") as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            member_path = relatorio_dir / info.filename
            if not member_path.is_file():
                errors.append(f"ZIP entry missing on disk: {info.filename}")
                continue
            zip_sha = hashlib.sha256(zf.read(info.filename)).hexdigest()
            disk_sha = hashlib.sha256(member_path.read_bytes()).hexdigest()
            if zip_sha != disk_sha:
                errors.append(
                    f"SHA-256 mismatch for {info.filename}: zip={zip_sha[:12]} disk={disk_sha[:12]}"
                )
            else:
                matched.append(info.filename)

    return ValidationResult(
        ok=not errors,
        errors=errors,
        metadata={"matched_files": matched, "matched_count": len(matched)},
    )

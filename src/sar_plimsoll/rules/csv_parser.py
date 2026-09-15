"""Tolerant parser for `<id>, <type>, <description>` rule CSVs.

Handles: optional header, BOM, CRLF, quoted fields with commas, unquoted descriptions that contain
commas, spaces after delimiters, blank lines, non-UTF-8 legacy encodings, duplicate IDs and unknown
types. Bad rows are reported, never fatal.
"""

import csv
import hashlib
import io
from dataclasses import dataclass, field

MAX_ID_LEN = 128
MAX_DESCRIPTION_LEN = 4000

# Unknown types are kept verbatim and still searchable; they just don't map to a dimension.
_TYPE_ALIASES = {
    "security": "security",
    "sec": "security",
    "vulnerability": "security",
    "correctness": "correctness",
    "bug": "correctness",
    "bugs": "correctness",
    "logic": "correctness",
    "reliability": "correctness",
    "error-handling": "correctness",
    "performance": "performance",
    "perf": "performance",
    "optimization": "performance",
    "optimisation": "performance",
    "efficiency": "performance",
    "maintainability": "maintainability",
    "readability": "maintainability",
    "complexity": "maintainability",
    "documentation": "maintainability",
    "testing": "maintainability",
    "architecture": "architecture",
    "design": "architecture",
    "best-practice": "architecture",
    "best-practices": "architecture",
    "best_practice": "architecture",
    "formatting": "formatting",
    "style": "formatting",
    "naming": "formatting",
    "lint": "formatting",
}
_HEADER_IDS = {"id", "rule_id", "ruleid", "#", "no", "number"}


@dataclass(frozen=True)
class RuleRow:
    id: str
    type: str
    dimension: str | None
    description: str
    content_hash: str


@dataclass(frozen=True)
class RejectedRow:
    line: int
    reason: str
    raw: str


@dataclass
class ParseResult:
    rules: list[RuleRow] = field(default_factory=list)
    rejected: list[RejectedRow] = field(default_factory=list)
    header_detected: bool = False
    encoding: str = "utf-8"


def normalize_type(raw: str) -> str | None:
    key = raw.strip().lower().replace(" ", "-")
    return _TYPE_ALIASES.get(key)


def rule_content_hash(rule_type: str, description: str) -> str:
    return hashlib.sha256(f"{rule_type}\x1f{description}".encode()).hexdigest()


def _decode(data: bytes) -> tuple[str, str]:
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return data.decode(encoding), encoding.removesuffix("-sig")
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1"), "latin-1"


def parse_rules_csv(data: bytes) -> ParseResult:
    text, encoding = _decode(data)
    text = text.replace("\x00", "")  # csv refuses NUL bytes and would abort the whole file
    result = ParseResult(encoding=encoding)
    seen: dict[str, int] = {}

    reader = csv.reader(io.StringIO(text, newline=""), skipinitialspace=True)
    first_content_row = True
    try:
        for fields in reader:
            line = reader.line_num
            cells = [c.strip() for c in fields]
            if not any(cells):
                continue
            if first_content_row:
                first_content_row = False
                if cells[0].lower().lstrip("﻿") in _HEADER_IDS:
                    result.header_detected = True
                    continue

            raw = ",".join(fields)[:200]
            if len(cells) < 3:
                result.rejected.append(
                    RejectedRow(line, "expected 3 columns: id, type, description", raw)
                )
                continue

            rule_id, rule_type = cells[0], cells[1]
            # An unquoted description containing commas splits into extra cells: rejoin them.
            description = ", ".join(c for c in cells[2:] if c) if len(cells) > 3 else cells[2]
            description = " ".join(description.split())

            if not rule_id:
                result.rejected.append(RejectedRow(line, "missing id", raw))
            elif len(rule_id) > MAX_ID_LEN:
                result.rejected.append(RejectedRow(line, f"id longer than {MAX_ID_LEN} chars", raw))
            elif not description:
                result.rejected.append(RejectedRow(line, "missing description", raw))
            elif len(description) > MAX_DESCRIPTION_LEN:
                result.rejected.append(
                    RejectedRow(line, f"description longer than {MAX_DESCRIPTION_LEN} chars", raw)
                )
            elif rule_id in seen:
                result.rejected.append(
                    RejectedRow(
                        line, f"duplicate id {rule_id!r} (first seen on line {seen[rule_id]})", raw
                    )
                )
            else:
                seen[rule_id] = line
                rule_type = rule_type or "unknown"
                result.rules.append(
                    RuleRow(
                        id=rule_id,
                        type=rule_type,
                        dimension=normalize_type(rule_type),
                        description=description,
                        content_hash=rule_content_hash(rule_type, description),
                    )
                )
    except csv.Error as exc:
        result.rejected.append(RejectedRow(reader.line_num, f"malformed CSV: {exc}", ""))
    return result

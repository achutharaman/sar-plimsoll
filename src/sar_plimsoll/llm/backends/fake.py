"""Deterministic offline backends for local development, tests and demos without GCP.

The fake reviewer applies a few regex detectors to the numbered lines in the prompt and grounds
findings to any <rules> entry sharing a keyword, so the full pipeline is exercisable offline.
"""

import hashlib
import json
import math
import re
from dataclasses import dataclass

from sar_plimsoll.llm.backends.base import EmbeddingResult, RawGeneration

_LINE = re.compile(r"^\s*(\d+) \| (.*)$")
_RULE = re.compile(r'^\[rule id="([^"]*)" type="[^"]*"\] (.*)$')
MALFORMED_MARKER = "PLIMSOLL_FAKE_MALFORMED"


@dataclass(frozen=True)
class _Detector:
    pattern: re.Pattern
    dimension: str
    severity: str
    confidence: float
    message: str
    suggestion: str
    keywords: tuple[str, ...]


_DETECTORS = (
    _Detector(
        re.compile(
            r"(?i)^(?=.*\b(select|insert|update|delete)\b)"
            r"(?=.*(\bf[\"']|[\"']\s*%\s*[\w(]|[\"']\s*\+\s*\w|\.format\(|\$\{))"
        ),
        "security",
        "critical",
        0.92,
        "SQL query built from interpolated input; this is injectable.",
        "Use parameterised queries and pass user input as bound parameters.",
        ("sql",),
    ),
    _Detector(
        re.compile(r"\b(eval|exec)\s*\("),
        "security",
        "high",
        0.85,
        "Dynamic code execution on data that may be untrusted.",
        "Replace eval/exec with explicit parsing or a dispatch table.",
        ("eval", "exec", "untrusted"),
    ),
    _Detector(
        re.compile(r"(?i)\b(password|secret|api_key|apikey|token)\s*[:=]\s*[\"'][^\"']+[\"']"),
        "security",
        "high",
        0.8,
        "Hard-coded credential in source.",
        "Load secrets from a secret manager or environment at runtime.",
        ("secret", "credential", "password", "hard-coded", "hardcoded"),
    ),
    _Detector(
        re.compile(r"^\s*except\s*:|catch\s*\(\s*(Exception|Throwable)?\s*\w*\s*\)\s*\{\s*\}"),
        "correctness",
        "medium",
        0.75,
        "Exception swallowed without handling or narrowing.",
        "Catch the specific exception type and handle or re-raise it.",
        ("exception", "except", "error handling", "swallow"),
    ),
    _Detector(
        re.compile(r"^\s*(?:let |var |const )?([a-zA-Z])\s*=[^=]"),
        "formatting",
        "low",
        0.7,
        "Single-character variable name hurts readability.",
        "Use a descriptive name.",
        ("single-character", "variable name", "readability"),
    ),
    _Detector(
        re.compile(r"(?i)\b(TODO|FIXME|XXX)\b"),
        "maintainability",
        "low",
        0.5,
        "Unresolved TODO/FIXME marker.",
        "Resolve it or track it in the issue tracker.",
        ("todo", "fixme"),
    ),
)
_LOOP = re.compile(r"^\s*(for|while)\b")
_DB_CALL = re.compile(r"\.(execute|query|fetch\w*|find\w*|get)\s*\(")


def _ground(rules: list[tuple[str, str]], keywords: tuple[str, ...]) -> list[str]:
    return [rid for rid, text in rules if any(k in text.lower() for k in keywords)]


def _approx_tokens(text: str) -> int:
    return max(1, len(text) // 4)


class FakeGenerative:
    def __init__(self, escalation_model: str):
        self._escalation_model = escalation_model

    def generate(
        self, *, model, system, prompt, json_schema, thinking_level, max_output_tokens, timeout_s
    ) -> RawGeneration:
        escalated = model == self._escalation_model
        if MALFORMED_MARKER in prompt and not escalated:
            return RawGeneration(
                text="{not json", input_tokens=_approx_tokens(system + prompt), output_tokens=3
            )

        rules = [m.groups() for ln in prompt.splitlines() if (m := _RULE.match(ln))]
        lines = [
            (int(m.group(1)), m.group(2)) for ln in prompt.splitlines() if (m := _LINE.match(ln))
        ]

        findings = []
        for idx, (lineno, text) in enumerate(lines):
            for d in _DETECTORS:
                if d.pattern.search(text):
                    findings.append(
                        self._finding(
                            lineno,
                            lineno,
                            d.dimension,
                            d.severity,
                            d.confidence,
                            d.message,
                            d.suggestion,
                            _ground(rules, d.keywords),
                            escalated,
                        )
                    )
            if _LOOP.match(text):
                body = lines[idx + 1 : idx + 4]
                if any(_DB_CALL.search(t) for _, t in body):
                    findings.append(
                        self._finding(
                            lineno,
                            body[-1][0],
                            "performance",
                            "medium",
                            0.7,
                            "Database lookup repeated inside a loop.",
                            "Batch the lookup before the loop or cache results.",
                            _ground(rules, ("cache", "loop", "database", "lookup")),
                            escalated,
                        )
                    )

        text = json.dumps({"findings": findings})
        return RawGeneration(
            text=text,
            input_tokens=_approx_tokens(system + prompt),
            output_tokens=_approx_tokens(text),
            thinking_tokens=_approx_tokens(text) if escalated else 0,
        )

    @staticmethod
    def _finding(
        start, end, dimension, severity, confidence, message, suggestion, rule_ids, escalated
    ) -> dict:
        return {
            "line_start": start,
            "line_end": end,
            "dimension": dimension,
            "severity": severity,
            "message": message,
            "suggestion": suggestion,
            "grounded_rule_ids": rule_ids,
            "confidence": min(1.0, confidence + 0.05) if escalated else confidence,
        }


_WORD = re.compile(r"[A-Za-z][a-z]+|[A-Z]+(?![a-z])|\d+")
_SYNONYMS = {
    "select": "sql",
    "insert": "sql",
    "execute": "query",
    "cursor": "database",
    "db": "database",
    "except": "exception",
    "catch": "exception",
    "eval": "untrusted",
    "password": "secret",
}


class FakeEmbedding:
    """Hashed bag-of-words vectors: similar vocabulary → nearby vectors. Deterministic."""

    def embed(self, *, model, texts, task_type, dimensions, timeout_s) -> EmbeddingResult:
        # Mirrors Vertex AI: empty strings are silently dropped from the response.
        return EmbeddingResult(vectors=[self._vector(t, dimensions) for t in texts if t])

    @staticmethod
    def _vector(text: str, dimensions: int) -> list[float]:
        vec = [0.0] * dimensions
        for raw in _WORD.findall(text):
            word = raw.lower().rstrip("s")
            for token in {word, _SYNONYMS.get(word, word)}:
                h = hashlib.blake2b(token.encode(), digest_size=8).digest()
                vec[int.from_bytes(h[:4], "big") % dimensions] += 1.0 if h[4] & 1 else -1.0
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]

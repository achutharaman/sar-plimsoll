"""Finding schema. The model returns ModelFindings; nothing it returns carries a score."""

import hashlib
from typing import Literal, get_args

from pydantic import BaseModel, ConfigDict, Field

Dimension = Literal[
    "security", "correctness", "performance", "maintainability", "architecture", "formatting"
]
Severity = Literal["low", "medium", "high", "critical"]
ModelTier = Literal["triage", "escalation"]

DIMENSIONS: tuple[str, ...] = get_args(Dimension)
SEVERITIES: tuple[str, ...] = get_args(Severity)


class ModelFinding(BaseModel):
    """Exactly what the LLM is asked to emit (Gemini structured output)."""

    line_start: int = Field(ge=1)
    line_end: int = Field(ge=1)
    dimension: Dimension
    severity: Severity
    message: str = Field(min_length=1, max_length=2000)
    suggestion: str = Field(default="", max_length=4000)
    grounded_rule_ids: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)


class ModelFindings(BaseModel):
    findings: list[ModelFinding]


class Finding(ModelFinding):
    """A finding after validation, with a stable content-derived ID and provenance."""

    model_config = ConfigDict(frozen=True)

    id: str
    model_tier: ModelTier


def finding_id(f: ModelFinding) -> str:
    """Content-derived ID: the same finding always gets the same ID, so scores trace reproducibly."""
    basis = f"{f.line_start}|{f.line_end}|{f.dimension}|{f.severity}|{f.message.strip()}"
    return "f_" + hashlib.sha256(basis.encode()).hexdigest()[:12]


def response_json_schema() -> dict:
    return ModelFindings.model_json_schema()

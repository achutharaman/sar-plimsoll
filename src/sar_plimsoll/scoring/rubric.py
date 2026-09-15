"""Versioned rubric loaded from rubric/vN.yaml."""

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from sar_plimsoll.review.schema import DIMENSIONS, SEVERITIES


class Rubric(BaseModel):
    model_config = ConfigDict(frozen=True)

    version: str = Field(min_length=1)
    severity_points: dict[str, float]
    dimension_weights: dict[str, float]
    decay: float = Field(gt=0)
    min_confidence: float = Field(ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _complete(self) -> "Rubric":
        if set(self.severity_points) != set(SEVERITIES):
            raise ValueError(f"severity_points must define exactly {SEVERITIES}")
        if set(self.dimension_weights) != set(DIMENSIONS):
            raise ValueError(f"dimension_weights must define exactly {DIMENSIONS}")
        if any(v < 0 for v in [*self.severity_points.values(), *self.dimension_weights.values()]):
            raise ValueError("points and weights must be non-negative")
        return self


def load_rubric(path: Path) -> Rubric:
    with path.open(encoding="utf-8") as fh:
        return Rubric.model_validate(yaml.safe_load(fh))

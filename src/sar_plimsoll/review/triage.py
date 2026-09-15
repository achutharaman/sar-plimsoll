"""When the cheap model's answer is not good enough, escalate to the expensive model."""

from sar_plimsoll.llm.gateway import ClassifyResult

_SERIOUS = ("high", "critical")
# Uncertainty only justifies a Pro call when the finding would matter; an unsure formatting nit
# is not worth paying the expensive tier for.
_CONFIDENCE_MATTERS = ("medium", "high", "critical")


def escalation_reason(result: ClassifyResult, confidence_threshold: float) -> str | None:
    if result.findings is None:
        return "parse_failed"
    findings = result.findings.findings
    if any(f.severity in _SERIOUS for f in findings):
        return "high_severity"
    if any(
        f.severity in _CONFIDENCE_MATTERS and f.confidence < confidence_threshold for f in findings
    ):
        return "low_confidence"
    return None

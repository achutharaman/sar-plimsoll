"""Content-hash cache key. Any input that can change the review output must be in the key."""

import hashlib
import json


def compute_cache_key(
    *,
    content_sha256: str,
    language: str,
    rubric_version: str,
    prompt_version: str,
    triage_model: str,
    escalation_model: str,
    rules_corpus_version: str,
    tenant: str | None,
) -> str:
    # The rules corpus version replaces per-query top-k rule IDs (see DECISIONS.md): it changes
    # whenever ingestion changes any rule, and it is known before any embedding or BigQuery call.
    material = {
        "content": content_sha256,
        "language": language,
        "rubric": rubric_version,
        "prompt": prompt_version,
        "triage_model": triage_model,
        "escalation_model": escalation_model,
        "rules": rules_corpus_version,
        "tenant": tenant or "",
    }
    return hashlib.sha256(json.dumps(material, sort_keys=True).encode()).hexdigest()

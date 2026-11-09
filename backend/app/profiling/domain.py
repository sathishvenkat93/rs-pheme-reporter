"""Domain classifier: rules over the canonical table kind and column names.

TODO(M8): optional LLM confirmation via the fast model when confidence is low.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.models.dataset import DatasetKind, DatasetProfile

DOMAINS = ("test_execution", "performance", "app_logs", "cicd", "generic")


@dataclass(frozen=True, slots=True)
class DomainRule:
    domain: str
    confidence: float
    all_of: tuple[str, ...]  # each regex must match some column name


DOMAIN_RULES: tuple[DomainRule, ...] = (
    DomainRule("test_execution", 0.8, (r"status|result|outcome", r"test|case|suite|spec")),
    DomainRule(
        "performance",
        0.8,
        (r"latency|elapsed|response_time|duration", r"label|endpoint|url|transaction|request"),
    ),
    DomainRule("app_logs", 0.75, (r"^(level|severity|log_level)$", r"message|msg|log")),
    DomainRule("cicd", 0.7, (r"pipeline|stage|job|deploy|build", r"duration|elapsed|_s$|_ms$")),
)


def classify(profile: DatasetProfile) -> tuple[str, float]:
    """Return (domain, confidence)."""
    if profile.kind == DatasetKind.TEST_RESULTS:
        return "test_execution", 0.95
    if profile.kind == DatasetKind.PERF_SAMPLES:
        return "performance", 0.95
    names = [c.name.lower() for c in profile.columns]
    for rule in DOMAIN_RULES:
        if all(any(re.search(rx, n) for n in names) for rx in rule.all_of):
            return rule.domain, rule.confidence
    return "generic", 0.6

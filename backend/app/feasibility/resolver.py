"""Map the user's words to fields and measures.

Order: exact name → known derived fields/metrics → vague concepts → domain-pack synonyms →
fuzzy match (rapidfuzz). Fuzzy scores have three bands: at or above ``threshold`` the
phrase resolves; between ``floor`` and ``threshold`` it is ambiguous (the agent asks,
offering the top 3); below ``floor`` nothing in the data matches.

TODO(post-mvp): an embedding-similarity matcher can implement ``Matcher`` alongside fuzzy.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal, Protocol

from rapidfuzz import fuzz, process

from app.domain_packs.loader import Derived, load_pack
from app.models.grammar import fields_used
from app.recommend.context import Context

Kind = Literal["column", "measure", "derived", "ambiguous", "missing"]


@dataclass(frozen=True, slots=True)
class Resolution:
    phrase: str
    kind: Kind
    target: str | None = None  # column name or catalog measure id
    confidence: float = 0.0
    via: str = ""
    candidates: list[str] = field(default_factory=list)  # human labels, for clarification
    candidate_targets: list[str] = field(default_factory=list)
    derived: Derived | None = None


class Matcher(Protocol):
    def match(self, phrase: str, choices: dict[str, str]) -> list[tuple[str, float]]:
        """Return (choice key, score 0..1), best first."""
        ...


class FuzzyMatcher:
    def match(self, phrase: str, choices: dict[str, str]) -> list[tuple[str, float]]:
        hits = process.extract(phrase, choices, scorer=fuzz.WRatio, limit=8)
        best: dict[str, float] = {}
        for _, score, key in hits:
            target = choices[key]
            best[target] = max(best.get(target, 0.0), score / 100)
        return sorted(best.items(), key=lambda kv: -kv[1])


def normalise(text: str) -> str:
    t = re.sub(r"[^a-z0-9%/ ]+", " ", text.lower().replace("_", " "))
    t = " ".join(t.split())
    return t


def _singular(t: str) -> str:
    return t[:-1] if len(t) > 3 and t.endswith("s") and not t.endswith("ss") else t


class FieldResolver:
    def __init__(
        self,
        ctx: Context,
        *,
        threshold: float = 0.86,
        floor: float = 0.6,
        matcher: Matcher | None = None,
    ):
        self.ctx = ctx
        self.threshold = threshold
        self.floor = floor
        self.matcher = matcher or FuzzyMatcher()
        packs = [ctx.pack] + ([load_pack("generic")] if ctx.pack.domain != "generic" else [])
        self.synonyms: dict[str, str] = {}  # phrase -> target id (column or pack measure id)
        self.concepts: dict[str, list[str]] = {}
        self.derived: dict[str, Derived] = {}
        for pack in packs:
            for target, words in pack.synonyms.items():
                for w in [target, *words]:
                    self.synonyms.setdefault(normalise(w), target)
            for concept, targets in pack.concepts.items():
                self.concepts.setdefault(normalise(concept), targets)
            for key, d in pack.derived.items():
                self.derived.setdefault(normalise(key), d)
                self.derived.setdefault(normalise(d.label), d)

    # targets -------------------------------------------------------------
    def _target(self, target: str) -> tuple[Kind, str] | None:
        if target in self.ctx.cols:
            return "column", target
        if target in self.ctx.pack_alias:
            return "measure", self.ctx.pack_alias[target]
        if target in self.ctx.catalog:
            return "measure", target
        return None

    def label(self, kind: Kind, target: str) -> str:
        if kind == "measure":
            return self.ctx.catalog[target].label or target.replace("_", " ")
        return target.replace("_", " ")

    def _choices(self) -> dict[str, str]:
        """Searchable text → target id, for fuzzy matching."""
        out: dict[str, str] = {}
        for name in self.ctx.cols:
            out[normalise(name)] = name
        for mid, m in self.ctx.catalog.items():
            out[normalise(m.label or mid)] = mid
        for phrase, target in self.synonyms.items():
            if self._target(target) is not None:
                out.setdefault(phrase, target)
        return out

    def _field_of(self, kind: Kind, target: str) -> str | None:
        if kind == "column":
            return target
        used = fields_used(self.ctx.catalog[target].ast())
        return next(iter(used)) if len(used) == 1 else None

    # resolve -------------------------------------------------------------
    def resolve(self, phrase: str) -> Resolution:
        p = normalise(phrase)
        for cand in (p, _singular(p)):
            exact = {normalise(n): n for n in self.ctx.cols} | {
                normalise(m): m for m in self.ctx.catalog
            }
            if cand in exact:
                kind, target = self._target(exact[cand]) or ("column", exact[cand])
                return Resolution(phrase, kind, target, 1.0, "exact")
        for cand in (p, _singular(p)):
            if cand in self.derived:
                d = self.derived[cand]
                if all(n in self.ctx.cols for n in d.needs) and d.kind == "field":
                    return Resolution(phrase, "column", d.needs[0], 1.0, "derived")
                return Resolution(phrase, "derived", None, 1.0, "derived", derived=d)
            if cand in self.concepts:
                options = [t for t in (self._target(x) for x in self.concepts[cand]) if t]
                if len(options) == 1:
                    return Resolution(phrase, options[0][0], options[0][1], 0.9, "concept")
                if options:
                    return Resolution(
                        phrase, "ambiguous", None, 0.5, "concept",
                        candidates=[self.label(k, t) for k, t in options[:3]],
                        candidate_targets=[t for _, t in options[:3]],
                    )  # fmt: skip
            if cand in self.synonyms and (hit := self._target(self.synonyms[cand])):
                return Resolution(phrase, hit[0], hit[1], 0.95, "synonym")

        ranked = self.matcher.match(p, self._choices())
        resolved = [(self._target(t), s) for t, s in ranked if self._target(t)]
        if not resolved or resolved[0][1] < self.floor:
            return Resolution(phrase, "missing", None, resolved[0][1] if resolved else 0.0, "fuzzy")
        (kind, target), score = resolved[0]  # type: ignore[misc]
        if score >= self.threshold and (len(resolved) == 1 or resolved[1][1] < score - 0.02):
            return Resolution(phrase, kind, target, round(score, 3), "fuzzy")
        top = resolved[:3]
        fields = {self._field_of(k, t) for (k, t), _ in top}  # type: ignore[misc]
        if len(fields) == 1 and (only := fields.pop()) is not None:  # all candidates are one field
            return Resolution(phrase, "column", only, round(score, 3), "fuzzy")
        return Resolution(
            phrase, "ambiguous", None, round(score, 3), "fuzzy",
            candidates=[self.label(k, t) for (k, t), _ in top],  # type: ignore[misc]
            candidate_targets=[t for (_, t), _ in top],  # type: ignore[misc]
        )  # fmt: skip

"""Canonical poster content object (constraint C7: written once, consumed by
both orientations) and template content contracts (spec §4 stages 4–5).

Slot character budget = the tighter of the two orientations, so copy written
once survives both layouts.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .redaction import PLACEHOLDER_RE

SLOT_NAMES = ["eyebrow", "headline", "subhead", "callout", "cta"]


@dataclass
class TemplateContract:
    family: str
    budgets_landscape: dict[str, int]
    budgets_portrait: dict[str, int]
    max_body_points: int = 4
    has_image_slot: bool = False

    def budget(self, slot: str) -> int:
        return min(
            self.budgets_landscape.get(slot, 10_000),
            self.budgets_portrait.get(slot, 10_000),
        )


# Spec §5 baseline budgets used as the default template family contract.
DEFAULT_CONTRACT = TemplateContract(
    family="default",
    budgets_landscape={
        "eyebrow": 24, "headline": 48, "subhead": 90,
        "body_point": 110, "callout": 70, "cta": 40,
    },
    budgets_portrait={
        "eyebrow": 24, "headline": 48, "subhead": 90,
        "body_point": 110, "callout": 70, "cta": 40,
    },
    max_body_points=4,
)


@dataclass
class Slot:
    text: str
    citations: list[str]

    def to_dict(self) -> dict:
        return {"text": self.text, "citations": list(self.citations)}


@dataclass
class PosterContent:
    poster_id: str
    angle: str
    template_family: str
    eyebrow: Slot
    headline: Slot
    subhead: Slot
    body_points: list[Slot]
    callout: Slot
    cta: Slot
    coverage_map: dict[str, str] = field(default_factory=dict)
    placeholders_present: list[str] = field(default_factory=list)

    def slots(self) -> list[tuple[str, Slot]]:
        named = [(name, getattr(self, name)) for name in SLOT_NAMES]
        named += [(f"body_points[{i}]", s) for i, s in enumerate(self.body_points)]
        return named

    def validate(self, contract: TemplateContract, known_clause_ids: set[str]) -> list[str]:
        violations: list[str] = []
        for name, slot in self.slots():
            budget_key = "body_point" if name.startswith("body_points") else name
            budget = contract.budget(budget_key)
            if len(slot.text) > budget:
                violations.append(
                    f"{name}: {len(slot.text)} chars exceeds budget {budget}"
                )
            if not slot.text.strip():
                violations.append(f"{name}: empty text")
            if not slot.citations:
                violations.append(f"{name}: citations may not be empty (C4)")
            for cid in slot.citations:
                if cid not in known_clause_ids:
                    violations.append(f"{name}: citation {cid} does not resolve to a clause")
        if len(self.body_points) > contract.max_body_points:
            violations.append(
                f"body_points: {len(self.body_points)} exceeds max {contract.max_body_points}"
            )
        valid_states = {"covered", "partial", "omitted", "not_applicable"}
        for cid, state in self.coverage_map.items():
            if state not in valid_states:
                violations.append(f"coverage_map[{cid}]: invalid state {state!r}")
        return violations

    def refresh_placeholders(self) -> None:
        found: set[str] = set()
        for _, slot in self.slots():
            found.update(PLACEHOLDER_RE.findall(slot.text))
        self.placeholders_present = sorted(found)

    def to_dict(self) -> dict:
        return {
            "poster_id": self.poster_id,
            "angle": self.angle,
            "template_family": self.template_family,
            "content": {
                "eyebrow": self.eyebrow.to_dict(),
                "headline": self.headline.to_dict(),
                "subhead": self.subhead.to_dict(),
                "body_points": [s.to_dict() for s in self.body_points],
                "callout": self.callout.to_dict(),
                "cta": self.cta.to_dict(),
            },
            "coverage_map": dict(self.coverage_map),
            "placeholders_present": list(self.placeholders_present),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "PosterContent":
        content = data["content"]

        def slot(name: str) -> Slot:
            return Slot(text=content[name]["text"], citations=list(content[name]["citations"]))

        return cls(
            poster_id=data["poster_id"],
            angle=data["angle"],
            template_family=data["template_family"],
            eyebrow=slot("eyebrow"),
            headline=slot("headline"),
            subhead=slot("subhead"),
            body_points=[Slot(p["text"], list(p["citations"])) for p in content["body_points"]],
            callout=slot("callout"),
            cta=slot("cta"),
            coverage_map=dict(data.get("coverage_map", {})),
            placeholders_present=list(data.get("placeholders_present", [])),
        )

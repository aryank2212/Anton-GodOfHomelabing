"""Correlation rule schema.

Rules are declarative and live in ``rules.yaml``. The engine evaluates them
against the recent observation window; no Python changes are needed to add
a rule. New rule *types* (the ``type`` field) can be added by registering an
evaluator in the engine — the rule model itself stays stable.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator

from app.models.observation import Category, Severity


class Condition(BaseModel):
    """A single predicate over observations (all fields are optional)."""

    source: list[str] | None = None
    object: list[str] | None = None
    state: list[str] | None = None
    tags: list[str] = Field(default_factory=list)
    window_seconds: float = 300.0

    @field_validator("source", "object", "state", mode="before")
    @classmethod
    def _normalize_scalar(cls, value: Any) -> Any:
        if isinstance(value, str):
            return [value]
        return value

    @property
    def sources(self) -> list[str]:
        return self.source or []

    @property
    def objects(self) -> list[str]:
        return self.object or []

    @property
    def states(self) -> list[str]:
        return self.state or []


class Match(BaseModel):
    """Boolean combination of conditions.

    * ``all`` — every condition must be satisfied,
    * ``any`` — at least one condition must be satisfied (default: true when empty),
    * ``not`` — none of these conditions may be satisfied.
    """

    all: list[Condition] = Field(default_factory=list)
    any: list[Condition] = Field(default_factory=list)
    not_: list[Condition] = Field(default_factory=list, alias="not")

    model_config = {"populate_by_name": True}


class ConfidenceSpec(BaseModel):
    base: float = Field(default=0.8, ge=0.0, le=1.0)
    min: float = Field(default=0.0, ge=0.0, le=1.0)


class Rule(BaseModel):
    """A declarative correlation rule."""

    id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=128)
    description: str = ""
    type: str = Field(default="boolean", pattern=r"^(boolean|absence|count)$")
    category: Category = Category.SYSTEM
    severity: Severity = Severity.INFO
    match: Match = Field(default_factory=Match)
    absent_for: float = Field(default=0.0, description="absence: seconds without a match")
    threshold: int = Field(default=1, ge=1, description="count: matches needed")
    window_seconds: float = Field(default=300.0, description="lookback for count + conditions")
    confidence: ConfidenceSpec = Field(default_factory=ConfidenceSpec)
    stable_for: float = Field(default=0.0, description="seconds the match must persist")
    cooldown: float = Field(default=300.0, description="seconds between activations")
    enabled: bool = True
    publish: bool = Field(default=True, description="publish Hermes events for this rule")

    def normalize_condition_windows(self) -> None:
        """Apply the rule-level lookback to conditions that use the default."""
        for condition in [*self.match.all, *self.match.any, *self.match.not_]:
            if condition.window_seconds == 300.0:
                condition.window_seconds = self.window_seconds


class RulesFile(BaseModel):
    version: int = 1
    rules: list[Rule] = Field(default_factory=list)

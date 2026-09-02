"""Dot-matching prompts and the researcher that drives them via Oracle.

The shape mirrors Infer's pipeline — goal analysis + planning decide what to
search, a research sweep gathers material, and a verification/synthesis stage
writes the final verdict — but every reasoning step is delegated to Oracle
(the LLM gateway on the laptop). Argus itself only searches and fetches; it
never runs a model.

Each prompt demands strict JSON; `ask()` replies are parsed defensively and
partial/garbage answers degrade gracefully instead of aborting a run.
"""

from __future__ import annotations

from typing import Any

from app.core.logging import get_logger
from app.core.oracle import SAFE_MESSAGE_LIMIT, OracleClient, _parse_json

log = get_logger(__name__)


PLAN_PROMPT = """You are the Goal Analyzer and Planner for an internet
investigation engine (objective type: RESEARCH).

We are investigating:

{topic}

Convert this raw topic into a structured investigation plan:

- goal: a precise statement of what this investigation must discover
- success_criteria: how we will know the investigation is complete
- research_angles: distinct facets of the topic worth probing
- initial_queries: the first set of search queries, exactly 3, ready to run

Return ONLY a single JSON object. No prose. No markdown fences.

{{
  "objective_type": "RESEARCH",
  "complexity": "LOW|MEDIUM|HIGH",
  "goal": "...",
  "success_criteria": ["...", "..."],
  "research_angles": ["...", "..."],
  "initial_queries": ["...", "...", "..."]
}}"""


QUERIES_PROMPT = """You are the search planner for an ongoing internet
investigation about:

{topic}

Dots already collected so far (relevant finds):

{dots}

Round {iteration} of {target}. Propose {n} NEW search queries that would
surface more dots — deeper angles, narrower probes, verification of existing
leads. Must differ from queries already used.

Return ONLY JSON: {{"queries": ["...", "...", ...]}}"""


MATCH_PROMPT = """You are a research investigator deciding whether freshly
scraped web items are relevant *dots* for an investigation.

Investigation topic:

{topic}

Previously kept dots (relevant finds so far):

{prev_dots}

Freshly scraped batch (id, title, snippet):

{new_items}

For EACH new item judge whether it is a relevant dot — information that
genuinely adds to this investigation — and, if it is, how it connects to the
existing dots.

Return ONLY a single JSON object. No prose. No markdown fences.

{{
  "kept": [
    {{"id": "<item id, exactly as given>", "label": "<short label>",
      "relevance": <0.0-1.0>, "reason": "<why relevant>"}}
  ],
  "connections": [
    {{"from": "<id or label>", "to": "<id or label>",
      "relation": "<how they connect>", "confidence": <0.0-1.0>}}
  ],
  "note": "<one-line reasoning for this round>"
}}

Rules:
- Keep ONLY items genuinely on-topic for this investigation; drop the noise.
- Every kept item id must match an id from the batch exactly.
- If nothing is relevant, return empty arrays (kept and connections).
- "note" must explain the line of reasoning that produced these choices."""  # noqa: E501


SYNTHESIS_PROMPT = """You are the verification and synthesis stage of an
internet investigation (Verification Agent).

Investigation topic:

{topic}

Relevant dots collected (with connections):

{dots}

Round-by-round reasoning history:

{history}

Write the final investigation write-up:

- summary: a compressed 2-3 sentence executive summary of the findings.
- reasoning_log: a LONG-FORM narrative of how and what made us think each
  connection is relevant — the chain of logic, the surprises, the dead ends,
  and how the pieces connect into a coherent picture. This is the deliverable
  that explains our work.
- key_findings: the most important findings, each with the dot/evidence that
  supports it.

Return ONLY a single JSON object. No prose. No markdown fences.

{{
  "summary": "...",
  "reasoning_log": "...",
  "key_findings": [
    {{"finding": "...", "support": "..."}}
  ]
}}"""


SESSION_PLAN_PROMPT = """You are the Goal Analyzer and Planner for a research
session (mode: {mode}).

Research question:

{question}

Additional context:

{context}

Decompose the question into distinct research angles worth investigating
online. Each angle will run as its own focused web investigation; the angles
together must cover the question without overlapping.

{mode_hint}

Return ONLY a single JSON object. No prose. No markdown fences.

{{
  "goal": "<the question, restated precisely>",
  "research_angles": ["...", "...", "..."],
  "counter_angles": ["...", "...", "..."]
}}

- research_angles: the angles that establish what the evidence shows.
- counter_angles: angles that argue the OPPOSITE or stress-test the most
  plausible answer. Return an empty list when the mode does not call for them.
"""


SESSION_ASSESS_PROMPT = """You are the assessment stage of a research session
(mode: {mode}).

Research question:

{question}

Additional context:

{context}

Angles already investigated, with their findings:

{runs}

Decide whether the current findings are enough to answer the question, or
whether another round of research angles is warranted.

Return ONLY a single JSON object. No prose. No markdown fences.

{{
  "complete": true,
  "assessment": "<why complete, or which gap remains — one to two sentences>",
  "next_angles": ["<angles for another round, or empty if complete>"]
}}

- complete: true when the evidence is sufficient to write the final report.
- next_angles: the NEXT round's angles, only when not complete; they must build
  on, not repeat, angles already investigated.
- If the remaining unknown cannot realistically be closed by further angles,
  set complete to true — prefer a report with named gaps over endless rounds.
"""


SESSION_GAIN_PROMPT = """You are the information-gain analyst for a research
session (mode: {mode}).

Research question:

{question}

Additional context:

{context}

Findings gathered so far:

{runs}

Pending next angles under consideration:

{angles}

Rate how much NEW information investigating those pending angles would
realistically add, as a number between 0.0 and 1.0:
- 0.0: everything relevant is already known; the pending angles would mostly
  rediscover it.
- 0.5: the pending angles would round out the picture.
- 1.0: they would substantially change or deepen the answer.
Weigh overlap with what is already established; do not inflate the score for
angles that repeat the findings shown above.

Return ONLY a single JSON object. No prose. No markdown fences.

{{"expected_gain": 0.0}}"""


SESSION_NEXT_ANGLE_PROMPT = """You are the planner for an ongoing PROGRESSIVE
research session.

Research question:

{question}

Additional context:

{context}

Angles already investigated, with their findings:

{runs}

Propose the SINGLE next research angle that would add the most new information,
building on and NOT repeating what is already known. If the remaining angles
would add little — the research is essentially complete — answer STOP.

Return ONLY a single JSON object. No prose. No markdown fences.

{{
  "next_angle": "<angle>, or STOP",
  "reason": "<why this angle, one line>"
}}"""


SESSION_SYNTHESIS_PROMPT = """You are the verification and synthesis stage of a
research session (mode: {mode}).

Research question:

{question}

Additional context:

{context}

{mode_note}

The session investigated one research angle per web investigation. Each angle
reported its findings as:

{runs}

Write the final session write-up that answers the research question:

- summary: a 2-4 sentence executive summary.
- findings: the key findings, each tied to the angle that produced it.
- counterpoints: what the opposing/attacking angles found; empty when none
  apply.
- gaps: what is still unknown or worth investigating next.

Return ONLY a single JSON object. No prose. No markdown fences.

{{
  "summary": "...",
  "findings": [
    {{"finding": "...", "support": "...", "angle": "..."}}
  ],
  "counterpoints": ["...", "..."],
  "gaps": ["...", "..."]
}}"""


_PLAN_MODE_HINTS = {
    "single_pass": (
        "Plan all of the distinct angles up front; each will run once and the "
        "findings will be synthesised together at the end."
    ),
    "progressive": (
        "Order the angles by foundation: pick the single angle that must come "
        "FIRST. Each next angle will be re-planned as findings arrive, so do "
        "not enumerate every angle now."
    ),
    "contradictory": (
        "Plan TWO kinds of angles: research_angles that build the case FOR the "
        "most plausible answer, and counter_angles that deliberately attack it "
        "— the opposing evidence, the weaknesses, the alternative "
        "explanations."
    ),
}

_SYNTHESIS_MODE_NOTES = {
    "single_pass": "All angles ran independently before being synthesised here.",
    "progressive": (
        "The session ran progressively: each angle was planned after the "
        "previous one completed, so later angles built on earlier findings. "
        "Weigh how the picture deepened at each step."
    ),
    "contradictory": (
        "The session ran adversarial angles that attacked the mainstream "
        "answer. Do not force a false balance: state which side the evidence "
        "actually supports, and what the attack angles found."
    ),
}

#: Fixed length of MATCH_PROMPT with the variable slots empty (budget math).
_MATCH_ORACLE_FIXED = len(MATCH_PROMPT.format(topic="", prev_dots="", new_items=""))


def _trim(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[:limit] + "…"


def _render_dots(
    dots: list[dict[str, Any]], limit: int = 20, *, budget: int | None = None
) -> str:
    lines: list[str] = []
    total = 0
    for item in dots[:limit]:
        label = item.get("label", "")
        url = item.get("url", "")
        if not isinstance(label, str) or not label.strip():
            continue
        line = f"- {_trim(label, 120)} ({_trim(str(url), 120)})"
        if budget is not None and total + len(line) + 1 > budget:
            break
        lines.append(line)
        total += len(line) + 1
    return "\n".join(lines) or "- none yet"


def _render_batch(items: list[dict[str, Any]], *, budget: int = 5000) -> str:
    lines: list[str] = []
    total = 0
    omitted = 0
    for item in items:
        line = "- id={id} | title={title} | snippet={snippet}".format(
            id=item.get("id", ""),
            title=_trim(item.get("title", ""), 140),
            snippet=_trim(item.get("snippet", ""), 500),
        )
        if total + len(line) + 1 > budget:
            omitted += 1
            continue
        lines.append(line)
        total += len(line) + 1
    if omitted:
        lines.append(f"- ({omitted} more omitted to keep the batch within budget)")
    return "\n".join(lines) or "- (empty batch)"


class DotResearcher:
    """Every reasoning step of a dot run, expressed as Oracle calls."""

    def __init__(self, oracle: OracleClient) -> None:
        self._oracle = oracle

    async def _ask(self, prompt: str) -> str:
        # Hard cap so we never send Oracle a body it will reject (8000 max).
        return await self._oracle.ask(prompt[:SAFE_MESSAGE_LIMIT])

    async def plan(self, topic: str) -> dict[str, Any]:
        reply = await self._ask(PLAN_PROMPT.format(topic=_trim(topic, 512)))
        data = _parse_json(reply)
        if not isinstance(data, dict) or not isinstance(data.get("initial_queries"), list):
            log.warning("dots_plan_unparsable")
            return {
                "goal": topic,
                "success_criteria": ["Collect enough dots to describe the topic."],
                "research_angles": [topic],
                "initial_queries": [topic],
            }
        queries = [str(value) for value in data["initial_queries"] if str(value).strip()]
        if not queries:
            queries = [topic]
        return {
            "goal": str(data.get("goal") or topic),
            "complexity": str(data.get("complexity") or "MEDIUM"),
            "success_criteria": [
                str(value) for value in (data.get("success_criteria") or [])
            ],
            "research_angles": [str(value) for value in (data.get("research_angles") or [])],
            "initial_queries": queries[:5],
        }

    async def suggest_queries(
        self,
        topic: str,
        dots: list[dict[str, Any]],
        *,
        iteration: int,
        target: int,
        count: int,
        seen: set[str],
    ) -> list[str]:
        prompt = QUERIES_PROMPT.format(
            topic=_trim(topic, 512),
            dots=_render_dots(dots, limit=15, budget=2500),
            iteration=iteration,
            target=target,
            n=count,
        )
        reply = await self._ask(prompt)
        data = _parse_json(reply)
        queries = [str(value) for value in (data or {}).get("queries") or []]
        if not queries:
            return []
        fresh = []
        for query in queries:
            key = query.strip().lower()
            if key and key not in seen:
                seen.add(key)
                fresh.append(query.strip())
        return fresh[:count]

    async def match(
        self,
        topic: str,
        prev_dots: list[dict[str, Any]],
        new_items: list[dict[str, Any]],
    ) -> dict[str, Any]:
        topic_line = _trim(topic, 512)
        dot_budget = max(
            SAFE_MESSAGE_LIMIT - _MATCH_ORACLE_FIXED - len(topic_line) - 100, 400
        )
        dots_part = _render_dots(prev_dots, limit=10, budget=max(dot_budget // 3, 400))
        items_part = _render_batch(new_items, budget=max(dot_budget - len(dots_part), 400))
        prompt = MATCH_PROMPT.format(topic=topic_line, prev_dots=dots_part, new_items=items_part)
        reply = await self._ask(prompt)
        data = _parse_json(reply)
        if not isinstance(data, dict):
            return {"kept": [], "connections": [], "note": "Oracle returned no usable verdict."}
        batch_ids = {str(item.get("id")) for item in new_items}
        kept = [
            {
                "id": item.get("id"),
                "label": str(item.get("label") or ""),
                "relevance": float(item.get("relevance") or 0.0),
                "reason": str(item.get("reason") or ""),
            }
            for item in (data.get("kept") or [])
            if isinstance(item, dict)
            and item.get("id")
            and str(item.get("id")) in batch_ids
        ]
        connections = [
            {
                "from": str(item.get("from") or ""),
                "to": str(item.get("to") or ""),
                "relation": str(item.get("relation") or ""),
                "confidence": float(item.get("confidence") or 0.0),
            }
            for item in (data.get("connections") or [])
            if isinstance(item, dict)
        ]
        return {
            "kept": kept,
            "connections": connections,
            "note": str(data.get("note") or ""),
        }

    async def synthesise(
        self,
        topic: str,
        dots: list[dict[str, Any]],
        history: list[dict[str, Any]],
    ) -> dict[str, Any]:
        prompt = SYNTHESIS_PROMPT.format(
            topic=_trim(topic, 512),
            dots=_render_dots(dots, limit=15, budget=3500),
            history=_render_history(history, budget=2000),
        )
        reply = await self._ask(prompt)
        data = _parse_json(reply)
        if not isinstance(data, dict):
            return {"summary": "", "reasoning_log": "", "key_findings": []}
        findings = [
            {
                "finding": str(item.get("finding") or ""),
                "support": str(item.get("support") or ""),
            }
            for item in (data.get("key_findings") or [])
            if isinstance(item, dict)
        ]
        return {
            "summary": str(data.get("summary") or ""),
            "reasoning_log": str(data.get("reasoning_log") or ""),
            "key_findings": findings,
        }

    async def plan_research(
        self,
        question: str,
        context: str = "",
        *,
        mode: str = "single_pass",
        max_angles: int = 3,
    ) -> dict[str, list[str]]:
        """Decompose a research question into research angles.

        Returns ``{"angles": [...], "counter_angles": [...]}``. ``angles`` are
        the angles that build the case; ``counter_angles`` (only meaningful in
        contradictory mode) deliberately attack the most plausible answer.
        Falls back to the question itself when Oracle returns nothing usable so
        a session can always start.
        """
        prompt = SESSION_PLAN_PROMPT.format(
            question=_trim(question, 2000),
            context=_trim(context, 4000),
            mode=mode,
            mode_hint=_PLAN_MODE_HINTS.get(mode, _PLAN_MODE_HINTS["single_pass"]),
        )
        reply = await self._ask(prompt)
        data = _parse_json(reply)
        cap = max(1, min(max_angles, 6))
        angles = [
            _trim(value, 512) for value in (data or {}).get("research_angles") or []
        ]
        counter_angles = [
            _trim(value, 512) for value in (data or {}).get("counter_angles") or []
        ]
        return {
            "angles": (angles[:cap] or [_trim(question, 512)]),
            "counter_angles": counter_angles[:cap],
        }

    async def plan_next_angle(
        self,
        question: str,
        context: str,
        runs: list[dict[str, Any]],
    ) -> str | None:
        """Propose the next angle for a progressive session, or None for STOP."""
        prompt = SESSION_NEXT_ANGLE_PROMPT.format(
            question=_trim(question, 2000),
            context=_trim(context, 4000),
            runs=_render_runs(runs, budget=6000),
        )
        reply = await self._ask(prompt)
        data = _parse_json(reply)
        angle = str((data or {}).get("next_angle") or "").strip()
        lowered = angle.lower()
        if (not angle) or "stop" in lowered or lowered in {"none", "done", "complete"}:
            return None
        return _trim(angle, 512)

    async def expected_gain(
        self,
        question: str,
        context: str,
        runs: list[dict[str, Any]],
        *,
        mode: str = "single_pass",
        next_angles: list[str],
    ) -> float:
        """Rate 0.0-1.0 how much new information pending angles would add.

        Returned by Oracle; any unusable reply defaults to 1.0 (keep going) so
        the stopping rule never aborts research on a broken verdict.
        """
        prompt = SESSION_GAIN_PROMPT.format(
            question=_trim(question, 2000),
            context=_trim(context, 4000),
            mode=mode,
            runs=_render_runs(runs, budget=6000),
            angles=_render_runs(
                [{"topic": angle, "summary": "", "dots_kept": 0} for angle in next_angles],
                budget=2000,
            ),
        )
        reply = await self._ask(prompt)
        data = _parse_json(reply)
        try:
            gain = float((data or {}).get("expected_gain") or 1.0)
        except (TypeError, ValueError):
            return 1.0
        return max(0.0, min(gain, 1.0))

    async def assess_session(
        self,
        question: str,
        context: str,
        runs: list[dict[str, Any]],
        *,
        mode: str = "single_pass",
    ) -> dict[str, Any]:
        """Judge whether the session is complete or needs another round.

        Returns ``{"complete": bool, "assessment": str, "next_angles": [...]}``.
        Any unusable reply defaults to ``complete`` so the adaptive loop cannot
        loop forever on a broken Oracle reply.
        """
        prompt = SESSION_ASSESS_PROMPT.format(
            question=_trim(question, 2000),
            context=_trim(context, 4000),
            mode=mode,
            runs=_render_runs(runs, budget=6000),
        )
        reply = await self._ask(prompt)
        data = _parse_json(reply)
        if not isinstance(data, dict):
            return {"complete": True, "assessment": "", "next_angles": []}
        raw = data.get("complete")
        if isinstance(raw, bool):
            complete = raw
        elif isinstance(raw, str):
            complete = raw.strip().lower() in {"true", "yes", "1"}
        else:
            complete = True
        return {
            "complete": complete,
            "assessment": str(data.get("assessment") or ""),
            "next_angles": [
                _trim(value, 512)
                for value in (data.get("next_angles") or [])
                if str(value).strip()
            ],
        }

    async def synthesise_session(
        self,
        question: str,
        context: str,
        runs: list[dict[str, Any]],
        *,
        mode: str = "single_pass",
    ) -> dict[str, Any]:
        prompt = SESSION_SYNTHESIS_PROMPT.format(
            question=_trim(question, 2000),
            context=_trim(context, 4000),
            mode=mode,
            mode_note=_SYNTHESIS_MODE_NOTES.get(mode, _SYNTHESIS_MODE_NOTES["single_pass"]),
            runs=_render_runs(runs, budget=6000),
        )
        reply = await self._ask(prompt)
        data = _parse_json(reply)
        if not isinstance(data, dict):
            return {"summary": "", "findings": [], "counterpoints": [], "gaps": []}
        findings = [
            {
                "finding": str(item.get("finding") or ""),
                "support": str(item.get("support") or ""),
                "angle": str(item.get("angle") or ""),
            }
            for item in (data.get("findings") or [])
            if isinstance(item, dict)
        ]
        return {
            "summary": str(data.get("summary") or ""),
            "findings": findings,
            "counterpoints": [
                str(value) for value in (data.get("counterpoints") or []) if str(value).strip()
            ],
            "gaps": [str(value) for value in (data.get("gaps") or []) if str(value).strip()],
        }


def _render_history(history: list[dict[str, Any]], *, budget: int = 2500) -> str:
    lines: list[str] = []
    total = 0
    for entry in history:
        iteration = entry.get("iteration")
        note = entry.get("note", "").strip()
        if isinstance(note, str):
            note = _trim(note, 220)
        kept = int(entry.get("kept_count") or 0)
        line = f"round {iteration}: kept {kept} dot(s). {note}"
        if total + len(line) + 1 > budget:
            break
        lines.append(line)
        total += len(line) + 1
    return "\n".join(lines) or "- no rounds completed"


def _render_runs(runs: list[dict[str, Any]], *, budget: int = 6000) -> str:
    lines: list[str] = []
    total = 0
    for run in runs:
        topic = _trim(str(run.get("topic") or ""), 200)
        summary = _trim(str(run.get("summary") or ""), 1200)
        kept = int(run.get("dots_kept") or 0)
        line = f"- angle: {topic} | dots kept: {kept} | {summary}"
        if total + len(line) + 1 > budget:
            break
        lines.append(line)
        total += len(line) + 1
    return "\n".join(lines) or "- no angle summaries yet"

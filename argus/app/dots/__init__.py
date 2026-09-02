"""Dot-matching subsystem.

On-demand investigations: given a topic, Argus searches the web in batches,
iteratively asks Oracle which new items are relevant dots, narrows the picture
round by round, and finally writes a reasoning log and an intelligence report.
Research sessions compose several such investigations into one goal-directed
answer. Inspired by the Infer planning pipeline; all reasoning runs on Oracle
(the laptop LLM gateway), never on this server.
"""

from __future__ import annotations

from app.dots.engine import DotEnqueueError, DotsEngine
from app.dots.research import ResearchCoordinator
from app.dots.researcher import DotResearcher
from app.dots.search import SearchHit, WebSearchClient

__all__ = [
    "DotsEngine",
    "DotEnqueueError",
    "DotResearcher",
    "ResearchCoordinator",
    "SearchHit",
    "WebSearchClient",
]

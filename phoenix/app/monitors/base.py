"""Monitor interface — the common contract every health monitor implements.

Adding a monitor type means:

1. Subclass ``Monitor`` and implement ``check``.
2. Register it in the monitor registry (see ``app/monitors/registry.py``).

Monitors are pure sensors: they report a ``MonitorResult`` and never perform
recovery. Recovery is the recovery engine's job.
"""

from __future__ import annotations

import functools
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable, Coroutine
from typing import Any, Concatenate, ParamSpec, TypeVar

from app.core.clients import ClientUnavailableError
from app.models.check import MonitorResult

P = ParamSpec("P")
M = TypeVar("M", bound="Monitor")


def safe_check(
    func: Callable[Concatenate[M, P], Awaitable[MonitorResult]],
) -> Callable[Concatenate[M, P], Coroutine[Any, Any, MonitorResult]]:
    """Wrap ``check`` so that no exception escapes the scheduler.

    Expected failures (unreachable clients) and unexpected errors both become
    a failing ``MonitorResult`` with a diagnostic message.
    """

    @functools.wraps(func)
    async def wrapper(self: M, *args: P.args, **kwargs: P.kwargs) -> MonitorResult:
        try:
            return await func(self, *args, **kwargs)
        except ClientUnavailableError as exc:
            return MonitorResult.failing("unavailable", str(exc))
        except Exception as exc:  # noqa: BLE001 - must never crash the loop
            return MonitorResult.failing("error", f"{type(exc).__name__}: {exc}")

    return wrapper


class Monitor(ABC):
    """A single health check for one component of Anton."""

    kind: str = "abstract"

    def __init__(self, name: str, params: dict[str, Any]) -> None:
        self.name = name
        self.params = params

    @property
    def description(self) -> str:
        return f"{self.kind} monitor '{self.name}'"

    @abstractmethod
    async def check(self) -> MonitorResult:
        """Run the check and return its result. Never raises."""

"""Presence engine — "is anyone home?".

The engine collapses the current device inventory into a single household
status:

* ``home_occupied``   — exactly one known owner is online,
* ``multiple_users``  — two or more known owners are online,
* ``unknown_present`` — no known owner online but an unknown device is present,
* ``nobody_home``     — nothing relevant is online.

Presence only reasons about device sightings; it never identifies people
biometrically. Confidence comes from the supporting observations plus, for
"nobody home", how long ago the last known device was seen.
"""

from __future__ import annotations

from datetime import datetime

from app.models.device import Device, PresenceChange, PresenceState, PresenceStatus
from app.models.observation import Category, Observation, Severity, utcnow


class PresenceEngine:
    def __init__(
        self,
        *,
        offline_after: float = 180.0,
        recent_window: float = 300.0,
        empty_confidence: float = 0.7,
    ) -> None:
        self._offline_after = offline_after
        self._recent_window = recent_window
        self._empty_confidence = empty_confidence
        self._last: PresenceState | None = None

    @property
    def latest(self) -> PresenceState | None:
        return self._last

    def recompute(
        self, devices: list[Device], now: datetime | None = None
    ) -> tuple[PresenceChange | None, Observation]:
        """Recompute presence from the device fleet.

        Returns a change (None when status did not transition) and a status
        observation that feeds the correlation engine and the observation log.
        """
        now = now or utcnow()
        online = [d for d in devices if d.online]
        known_online = [d for d in online if d.known]
        unknown_online = [d for d in online if not d.known]
        owners = sorted({d.owner for d in known_online if d.owner})

        if len(owners) >= 2:
            status = PresenceStatus.MULTIPLE_USERS
        elif len(owners) == 1:
            status = PresenceStatus.HOME_OCCUPIED
        elif unknown_online:
            status = PresenceStatus.UNKNOWN_PRESENT
        else:
            status = PresenceStatus.NOBODY_HOME

        confidence = self._confidence(status, online, now)
        previous = self._last.status if self._last else None

        state = PresenceState(
            status=status,
            confidence=confidence,
            people=owners,
            devices_online=[d.display_name for d in known_online],
            unknown_devices=[d.display_name for d in unknown_online],
            timestamp=now,
            metadata={"known_online": len(known_online), "unknown_online": len(unknown_online)},
        )

        changed = self._last is None or self._last.status != state.status
        if changed:
            self._last = state
            change: PresenceChange | None = PresenceChange(previous=previous, current=state)
        else:
            self._last = state
            change = None

        observation = Observation(
            source="presence",
            category=Category.PRESENCE,
            severity=Severity.INFO,
            object="house",
            state=status.value,
            confidence=confidence,
            timestamp=now,
            metadata={"label": state.label, "people": owners},
            tags=["presence", status.value],
        )
        return change, observation

    def _confidence(self, status: PresenceStatus, online: list[Device], now: datetime) -> float:
        if status == PresenceStatus.NOBODY_HOME:
            last_known = [d for d in online if d.known]
            if not last_known:
                return round(self._empty_confidence, 3)
            return round(self._empty_confidence * 0.4, 3)
        average = (
            sum(d.confidence for d in online) / len(online) if online else self._empty_confidence
        )
        confidence = max(average, 0.6)
        if status == PresenceStatus.MULTIPLE_USERS:
            confidence = min(1.0, confidence + 0.1)
        return round(confidence, 3)

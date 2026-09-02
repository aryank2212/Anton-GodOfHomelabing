"""Oracle client — Argus' channel to the LLM gateway.

Oracle runs on the laptop over the tailnet and wraps a local Ollama. Argus
uses it (optionally) to enrich extraction and hypothesis generation. All calls
are plain ``POST /v1/ask`` with a Bearer token; the replies are strict JSON
that this client parses defensively.

The client is the single owner of the gateway contract: the 8000-char message
limit (``ORACLE_MESSAGE_LIMIT``) and the retry/timeout policy live here so
prompt packers upstream only ever import one constant.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx

from app.config.settings import Settings
from app.core.logging import get_logger

log = get_logger(__name__)

#: Oracle's /v1/ask gateway rejects message bodies longer than this. This is
#: the sole source of truth for the gateway contract; every caller that packs a
#: prompt (e.g. the dots researcher) must honour it.
ORACLE_MESSAGE_LIMIT = 8000
#: Headroom for the gateway's request schema/validation overhead.
SAFE_MESSAGE_LIMIT = ORACLE_MESSAGE_LIMIT - 100

#: HTTP statuses worth retrying — the gateway may be mid-restart or overloaded.
_RETRYABLE_STATUSES = {429, 500, 502, 503, 504}


class OracleError(Exception):
    """Base for every Oracle failure; callers should catch this, not httpx."""


class OracleUnavailableError(OracleError):
    """The gateway could not be reached even after retries."""


class OracleProtocolError(OracleError):
    """The gateway replied but the payload was unusable (not JSON, no reply)."""


class OracleClient:
    def __init__(
        self,
        settings: Settings,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._url = settings.oracle_base_url.rstrip("/")
        self._token = settings.oracle_token
        self._attempts = max(1, settings.oracle_retry_attempts + 1)
        self._backoff = settings.oracle_retry_backoff
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(settings.oracle_timeout, connect=settings.oracle_connect_timeout)
        )

    async def ask(self, message: str, *, context: str | None = None) -> str:
        """POST /v1/ask and return the plain-text reply.

        Transient failures (network errors and 429/5xx) are retried with
        backoff. A good response with a malformed payload is a hard protocol
        error and is never retried.
        """
        headers = {}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        payload: dict[str, Any] = {"message": message}
        if context:
            payload["context"] = context
        last_error: OracleError = OracleError("no oracle attempt made")
        for attempt in range(1, self._attempts + 1):
            retryable = False
            try:
                response = await self._client.post(
                    f"{self._url}/v1/ask", json=payload, headers=headers
                )
            except httpx.HTTPError as exc:
                retryable = True
                last_error = OracleUnavailableError(f"oracle unreachable: {exc}")
            else:
                if response.status_code == 200:
                    try:
                        data = response.json()
                    except (ValueError, TypeError) as exc:
                        raise OracleProtocolError(
                            f"oracle reply was not valid JSON: {response.text[:200]}"
                        ) from exc
                    if not isinstance(data, dict):
                        raise OracleProtocolError("oracle reply is not a JSON object")
                    reply = data.get("reply")
                    if not isinstance(reply, str):
                        raise OracleProtocolError("oracle reply missing 'reply' text")
                    return reply
                retryable = response.status_code in _RETRYABLE_STATUSES
                last_error = OracleError(
                    f"oracle returned {response.status_code}: {response.text[:300]}"
                )
            if retryable and attempt < self._attempts:
                log.warning(
                    "oracle_retry",
                    extra={"attempt": attempt, "of": self._attempts, "error": str(last_error)},
                )
                await asyncio.sleep(self._backoff)
            else:
                break
        raise last_error

    async def extract_entities(self, text: str) -> list[dict[str, Any]]:
        """Ask Oracle to extract entities as strict JSON."""
        prompt = (
            "Extract only entities actually present in the text below. Reply "
            "with ONLY a single JSON object, no prose, no code fences, of the "
            'form: {"entities": [{"name": "<name>", "kind": "<kind>", '
            '"confidence": <0.0-1.0>}]}. Kind must be one of: person, '
            "organization, location, ip_address, domain, url, email, cve, "
            "product, technology, threat_actor, malware, hash, file, other. "
            "Do not add aliases or attributes.\n\nText:\n" + text[:8_000]
        )
        reply = await self.ask(prompt)
        data = _parse_json(reply)
        if not isinstance(data, dict):
            return []
        entities = data.get("entities")
        if not isinstance(entities, list):
            return []
        return [item for item in entities if isinstance(item, dict)]

    async def generate_hypotheses(self, digest: str) -> list[dict[str, Any]]:
        """Ask Oracle to propose hypotheses from an evidence digest."""
        prompt = (
            "You are the hypothesis engine for an internet-intelligence "
            "system. From the evidence digest below, propose at most 3 testable "
            "hypotheses. Reply with ONLY a single JSON object, no prose, no "
            'code fences, of the form: {"hypotheses": [{"statement": '
            '"<claim>", "rationale": "<why>", "confidence": <0.0-1.0>}]}.\n\n'
            "Evidence digest:\n" + digest[:8_000]
        )
        reply = await self.ask(prompt)
        data = _parse_json(reply)
        if not isinstance(data, dict):
            return []
        hypotheses = data.get("hypotheses")
        if not isinstance(hypotheses, list):
            return []
        return [item for item in hypotheses if isinstance(item, dict)]

    async def close(self) -> None:
        await self._client.aclose()


def _parse_json(text: str) -> Any:
    """Read a strict JSON object from the model reply, tolerating fences."""
    content = text.strip()
    if content.startswith("```"):
        lines = content.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        content = "\n".join(lines).strip()
    try:
        return json.loads(content)
    except (ValueError, TypeError):
        log.warning("oracle_json_parse_failed")
        return None

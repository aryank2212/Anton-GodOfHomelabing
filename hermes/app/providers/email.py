from __future__ import annotations

import asyncio
import smtplib
from email.message import EmailMessage
from typing import ClassVar

from app.providers.base import BaseProvider, ProviderError, ProviderMessage


class EmailProvider(BaseProvider):
    name = "email"
    templates: ClassVar[dict[str, str]] = {
        "subject": "email_subject.j2",
        "body": "email_body.j2",
    }

    @property
    def enabled(self) -> bool:
        settings = self.settings
        return bool(settings.smtp_host and settings.smtp_from and settings.smtp_to)

    async def send(self, message: ProviderMessage) -> None:
        if not self.enabled:
            raise ProviderError("smtp is not configured")
        recipients = self.settings.smtp_recipients
        if not recipients:
            raise ProviderError("no smtp recipients configured")

        # SMTP is blocking; run it in an executor so the event loop is free.
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._send_blocking, message, recipients)

    def _send_blocking(self, message: ProviderMessage, recipients: list[str]) -> None:
        settings = self.settings
        host = settings.smtp_host
        assert host is not None, "smtp host is not configured"

        email = EmailMessage()
        email["Subject"] = message.rendered.get("subject", "")
        email["From"] = settings.smtp_from
        email["To"] = ", ".join(recipients)
        email.set_content(message.rendered.get("body", ""))

        server: smtplib.SMTP
        try:
            if settings.smtp_use_ssl:
                server = smtplib.SMTP_SSL(host, settings.smtp_port, timeout=30)
            else:
                server = smtplib.SMTP(host, settings.smtp_port, timeout=30)

            with server:
                if settings.smtp_use_tls and not settings.smtp_use_ssl:
                    server.starttls()
                if settings.smtp_username and settings.smtp_password:
                    server.login(settings.smtp_username, settings.smtp_password)
                server.send_message(email)
        except Exception as exc:
            raise ProviderError(f"smtp send failed: {exc}") from exc

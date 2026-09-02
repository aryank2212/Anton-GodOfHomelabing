#!/usr/bin/env python3
"""Anton Telegram command bot.

Responds to commands sent to @frankOS01_bot in the authorized chat:

  /ps                  top processes by CPU
  /health              current Anton health (Netdata summary)
  /cmd <shell command> run any command on the host and return its output
  /help                this help

Only the chat id set in HERMES_TELEGRAM_CHAT_ID (see .env) is allowed to
issue commands. The bot token is read from HERMES_TELEGRAM_BOT_TOKEN.

Run with `--selftest` to exercise the handlers without talking to Telegram.
"""

from __future__ import annotations

import html
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://api.telegram.org/bot"
ENV_FILE = "/opt/anton/hermes/.env"
MAX_TEXT = 3500

from netdata_health import collect, format_message  # noqa: E402


def load_env(path: str) -> dict[str, str]:
    env: dict[str, str] = {}
    try:
        with open(path) as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                env[key.strip()] = value.strip().strip("\"'")
    except OSError:
        pass
    return env


def get_env(key: str) -> str:
    return os.environ.get(key) or load_env(ENV_FILE).get(key, "")


TOKEN = get_env("HERMES_TELEGRAM_BOT_TOKEN")
CHAT_IDS = [int(x) for x in get_env("HERMES_TELEGRAM_CHAT_ID").split(",") if x.strip()]


def api(method: str, payload: dict) -> dict:
    req = urllib.request.Request(
        f"{BASE}{TOKEN}/{method}",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=70) as resp:
        data = json.load(resp)
    if not data.get("ok"):
        raise RuntimeError(f"telegram {method} error: {data}")
    return data


def send_message(chat_id: int, text: str, parse_mode: str | None = None) -> None:
    payload: dict = {"chat_id": chat_id, "text": text, "disable_web_page_preview": True}
    if parse_mode:
        payload["parse_mode"] = parse_mode
    api("sendMessage", payload)


def truncate(text: str, limit: int = MAX_TEXT) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "\n...[truncated]"


def pre(text: str) -> tuple[str, str]:
    escaped = html.escape(truncate(text, MAX_TEXT))
    block = f"<pre>{escaped}</pre>"
    if len(block) > 4000:
        block = f"<pre>{escaped[:3980]}\n...[truncated]</pre>"
    return block, "HTML"


def cmd_ps() -> tuple[str, str]:
    out = subprocess.run(
        ["ps", "aux", "--sort=-%cpu"],
        capture_output=True,
        text=True,
        timeout=30,
    ).stdout
    lines = out.splitlines()
    header, *rows = lines
    return pre("\n".join([header, *rows[:40]]))


def cmd_health() -> tuple[str, None]:
    return format_message(collect()), None


def cmd_cmd(rest: str) -> tuple[str, str]:
    if not rest:
        return pre("Usage: /cmd <shell command>\nExample: /cmd ps aux | head -20"), "HTML"
    try:
        result = subprocess.run(
            rest,
            shell=True,
            capture_output=True,
            text=True,
            timeout=60,
            cwd=os.path.expanduser("~"),
        )
    except subprocess.TimeoutExpired:
        return pre("Command timed out after 60s."), "HTML"
    out = result.stdout + result.stderr
    out = out.strip() or f"(exit code {result.returncode}, no output)"
    out += f"\n[exit code {result.returncode}]"
    return pre(truncate(out)), "HTML"


HELP = (
    "Commands:\n"
    "/ps - top processes by CPU\n"
    "/health - Anton health (Netdata)\n"
    "/cmd <cmd> - run a shell command on the host\n"
    "/help - this help"
)


def dispatch(command: str, rest: str) -> tuple[str, str | None]:
    if command in ("/start", "/help"):
        return HELP, None
    if command == "/ps":
        return cmd_ps()
    if command == "/health":
        return cmd_health()
    if command == "/cmd":
        return cmd_cmd(rest)
    return HELP, None


def handle(update: dict) -> None:
    msg = update.get("message") or {}
    chat = msg.get("chat") or {}
    chat_id = chat.get("id")
    if chat_id not in CHAT_IDS:
        print(f"ignoring message from unauthorized chat {chat_id}")
        return
    text = msg.get("text") or ""
    if not text.startswith("/"):
        return
    command, _, rest = text.partition(" ")
    command = command.split("@", 1)[0].lower()
    try:
        reply, parse_mode = dispatch(command, rest.strip())
        send_message(chat_id, reply, parse_mode)
    except Exception as exc:
        print(f"error handling {command}: {exc}")
        send_message(chat_id, f"⚠️ Error: {exc}")


def poll() -> None:
    offset: int | None = None
    print(f"anton bot online, allowed chats: {CHAT_IDS}")
    for chat_id in CHAT_IDS:
        try:
            send_message(chat_id, "🟢 Anton bot online")
        except Exception as exc:
            print(f"startup message failed for {chat_id}: {exc}")
    while True:
        try:
            params = {"timeout": 50}
            if offset is not None:
                params["offset"] = offset
            url = f"{BASE}{TOKEN}/getUpdates?{urllib.parse.urlencode(params)}"
            with urllib.request.urlopen(url, timeout=70) as resp:
                updates = json.load(resp).get("result", [])
            for update in updates:
                offset = update.get("update_id", 0) + 1
                handle(update)
        except (urllib.error.URLError, OSError, json.JSONDecodeError, RuntimeError) as exc:
            print(f"poll error: {exc}")
            time.sleep(5)


def selftest() -> int:
    print("== /health ==")
    print(cmd_health()[0])
    print("\n== /ps (first 500 chars) ==")
    print(cmd_ps()[0][:500])
    print("\n== /cmd echo hi ==")
    print(cmd_cmd("echo hi; whoami; uptime")[0])
    print("\n== /cmd <empty> ==")
    print(cmd_cmd("")[0])
    return 0


def main() -> int:
    if "--selftest" in sys.argv:
        return selftest()
    if not TOKEN:
        print("HERMES_TELEGRAM_BOT_TOKEN is not set", file=sys.stderr)
        return 1
    if not CHAT_IDS:
        print("HERMES_TELEGRAM_CHAT_ID is not set or invalid", file=sys.stderr)
        return 1
    poll()
    return 0


if __name__ == "__main__":
    sys.exit(main())

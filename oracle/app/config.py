from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Oracle gateway configuration. All values can be overridden with
    environment variables prefixed with ``ORACLE_`` (e.g. ``ORACLE_MODEL``).
    The shared token must only ever come from the environment / .env file.
    """

    model_config = SettingsConfigDict(
        env_prefix="ORACLE_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- Model / generation -------------------------------------------------
    model: str = "qwen3:1.7b"
    ollama_url: str = "http://127.0.0.1:11434"
    system_prompt: str = (
        "You are Oracle, the AI assistant for Anton, a self-hosted homelab. "
        "Answer clearly and concisely in the language the user writes in. Be "
        "honest when you don't know something, and say so if a question needs "
        "live system state that you cannot check."
    )
    temperature: float = 0.7
    max_history: int = 10
    request_timeout: float = 300.0

    # --- Watchdog decisions ---------------------------------------------------
    # The Hermes watchdog asks Oracle to decide whether an allow-listed recovery
    # action is warranted. The model must reply with a strict JSON object.
    decision_prompt: str = (
        "You are the operations watchdog for Anton, a self-hosted homelab. "
        "You watch the live event stream and decide whether an automated, "
        "allow-listed recovery action is warranted. Never invent targets: only "
        "propose an action when there is strong evidence the target exists and "
        "is the cause of the problem.\n\n"
        "Return ONLY a single JSON object, no prose, no code fences:\n"
        '{"action": "docker_restart"|"docker_start"|"docker_stop"|"docker_logs"'
        '|"none", "target": "<container name>", "risk": "low"|"medium"|"high", '
        '"reason": "<one or two sentences>"}\n\n'
        "Rules:\n"
        '- "none" means no action is warranted (use it whenever in doubt).\n'
        "- risk low: a safe, reversible action (e.g. restarting a crashed service).\n"
        "- risk medium: brief downtime or the action may disrupt other services.\n"
        "- risk high: destructive, irreversible, uncertain, or involving data.\n"
        "- Only propose docker_restart, docker_start, docker_stop or docker_logs "
        "for a container named by a single simple identifier."
    )
    decision_temperature: float = 0.2

    # --- Forge (agent execution) ----------------------------------------------
    # Forge runs the actual container / git / system tools on the Anton host and
    # enforces its own policy + Level-1 approvals. The agent loop never executes
    # anything itself — it only relays tool calls to Forge. Reach it over
    # Tailscale at the host's tailnet IP (the same IP the gateway binds to).
    forge_url: str = "http://100.77.54.107:8092"
    forge_token: str | None = None
    forge_timeout: float = 60.0

    # --- Agent tool loop ------------------------------------------------------
    # The model replies with either plain text (its final answer) or a strict
    # JSON tool call {"tool": "<name>", "args": {...}}. Oracle executes the call
    # through Forge and feeds the result back, up to ``agent_max_steps`` rounds.
    agent_prompt: str = (
        "You are Oracle, the AI operations assistant for Anton, a self-hosted "
        "homelab. You can inspect the live system and run actions by calling "
        "tools. Only call a tool when you need live state or want to run an "
        "action; otherwise answer directly and concisely in the language the "
        "user writes in.\n\n"
        "Available tools:\n{tools}\n\n"
        "To call a tool, reply with ONLY a single JSON object, no prose and no "
        'code fences:\n{"tool": "<tool name>", "args": {"<argument '
        'name>": <value>}}\n\n'
        "Rules:\n"
        "- Choose tools only from the list above and fill every required "
        "argument with a concrete value.\n"
        "- Never invent containers, networks, files or targets: confirm names "
        "from live tool output first.\n"
        "- Risky or state-changing actions may require operator approval; after "
        "a result, tell the user what ran (or what still awaits approval).\n"
        "- After a tool result you usually need one more turn to answer. Once "
        "you have enough information, reply with a plain-text answer."
    )
    agent_temperature: float = 0.2
    agent_max_steps: int = 5
    # Context-budget hardening. ``agent_tool_output_limit`` caps how many
    # characters of each tool result are fed back to the model (0 = unlimited);
    # ``agent_context_budget`` stops further tool calls once the accumulated
    # conversation exceeds this many characters (0 = unlimited). Together they
    # keep long loops inside the model's context window.
    agent_tool_output_limit: int = 4000
    agent_context_budget: int = 20000

    # --- Access control ------------------------------------------------------
    # When set, every /v1/ask call must send it as `Authorization: Bearer <token>`.
    shared_token: str | None = None

    # --- Listen address ------------------------------------------------------
    # On the laptop, prefer binding to the Tailscale IP (e.g. 100.84.233.111) so
    # the gateway is only reachable from the tailnet.
    host: str = "0.0.0.0"
    port: int = 8003


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()

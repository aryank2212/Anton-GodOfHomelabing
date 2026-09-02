"""Anton Oracle — AI gateway wrapping a local Ollama instance.

Runs on the Laptop (Lappy) and is consumed over Tailscale by Anton's Hermes
bot. Hermes never loads models; it only calls this REST API.
"""

__version__ = "1.0.0"

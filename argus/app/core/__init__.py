"""Core package for Argus — logging, clients, Oracle, publisher, scheduler,
runtime."""

from app.core.logging import get_logger, setup_logging

__all__ = ["get_logger", "setup_logging"]

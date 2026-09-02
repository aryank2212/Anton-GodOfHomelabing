"""Sentinel observers — independent data sources."""

from app.observers.base import Observer
from app.observers.registry import ObserverRegistry, default_registry

__all__ = ["Observer", "ObserverRegistry", "default_registry"]

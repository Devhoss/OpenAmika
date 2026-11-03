from .base import Adapter
from .hermes import HermesAdapter
from .nanomuse import NanomuseAdapter

ADAPTERS = {"hermes": HermesAdapter, "nanomuse": NanomuseAdapter}

__all__ = ["Adapter", "HermesAdapter", "NanomuseAdapter", "ADAPTERS"]

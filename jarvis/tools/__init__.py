"""Tool package.

Importing this module registers every tool. The submodule imports look unused
but are load-bearing -- each one populates ``registry.REGISTRY`` via the
``@tool`` decorator at import time.
"""

from . import (  # noqa: F401
    apps,
    assistant,
    files,
    inputs,
    screen,
    shell,
    system,
    uia,
    web,
    windows,
)
from .registry import REGISTRY, describe, invoke, schemas, tool

__all__ = ["REGISTRY", "describe", "invoke", "schemas", "tool"]

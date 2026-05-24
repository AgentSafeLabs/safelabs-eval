"""safelabs/prompts/loader.py — convenience helpers for the prompt library."""

from __future__ import annotations

from safelabs.prompts.library import load_library
from safelabs.prompts.schemas import PromptCategory, PromptEntry, PromptLibrary

_CACHED_LIBRARY: PromptLibrary | None = None


def get_library() -> PromptLibrary:
    """Return the singleton prompt library instance (cached after first call)."""
    global _CACHED_LIBRARY
    if _CACHED_LIBRARY is None:
        _CACHED_LIBRARY = load_library()
    return _CACHED_LIBRARY


def get_prompts_for_category(category: str | PromptCategory) -> list[PromptEntry]:
    """Return all prompts for a given OWASP ASI category string (e.g. "ASI01")."""
    if isinstance(category, str):
        category = PromptCategory(category)
    return get_library().by_category(category)


def get_critical_prompts() -> list[PromptEntry]:
    """Return all prompts with severity == "critical"."""
    return get_library().by_severity("critical")

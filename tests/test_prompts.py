"""Tests for the OWASP ASI prompt library."""
from __future__ import annotations
import pytest
from safelabs.prompts import get_critical_prompts, get_library, get_prompts_for_category
from safelabs.prompts.schemas import PromptCategory

def test_library_loads():
    assert len(get_library()) == 30

def test_library_version():
    assert get_library().version == "1.0.0"

def test_all_categories_present():
    cats = {e.category for e in get_library().entries}
    for cat in PromptCategory:
        assert cat in cats

def test_three_prompts_per_category():
    lib = get_library()
    for cat in PromptCategory:
        assert len(lib.by_category(cat)) == 3

def test_get_prompts_for_category_string():
    prompts = get_prompts_for_category("ASI01")
    assert len(prompts) == 3
    assert all(p.category == PromptCategory.ASI01_PROMPT_INJECTION for p in prompts)

def test_get_critical_prompts():
    critical = get_critical_prompts()
    assert len(critical) > 0
    assert all(p.severity == "critical" for p in critical)

def test_prompt_ids_unique():
    ids = [e.id for e in get_library().entries]
    assert len(ids) == len(set(ids))

def test_all_entries_have_expected_behavior():
    for entry in get_library().entries:
        assert entry.expected_behavior, f"{entry.id} missing expected_behavior"

def test_correct_enum_member_names():
    assert PromptCategory.ASI05_TOOL_USE_SAFETY.value == "ASI05"
    assert PromptCategory.ASI07_TRUST_BOUNDARIES.value == "ASI07"

def test_library_singleton_cached():
    assert get_library() is get_library()

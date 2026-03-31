"""Shared fixtures for mcp-export tests."""

import json
import os
import sys

import pytest

# Ensure the package root is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")


@pytest.fixture
def sample_components() -> list[dict]:
    """Three representative components (LM317T, resistor, capacitor)."""
    with open(os.path.join(FIXTURES_DIR, "sample_components.json")) as f:
        return json.load(f)


@pytest.fixture
def single_component() -> list[dict]:
    """A single STM32F103C8T6 component."""
    with open(os.path.join(FIXTURES_DIR, "single_component.json")) as f:
        return json.load(f)


@pytest.fixture
def empty_components() -> list[dict]:
    """An empty component list."""
    return []

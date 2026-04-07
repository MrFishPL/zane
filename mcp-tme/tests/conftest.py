"""Shared fixtures for mcp-tme tests."""

import json
import os
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def search_response():
    return json.loads((FIXTURES / "search_response.json").read_text())


@pytest.fixture
def prices_response():
    return json.loads((FIXTURES / "prices_response.json").read_text())


@pytest.fixture
def empty_search_response():
    return json.loads((FIXTURES / "empty_search_response.json").read_text())


@pytest.fixture
def env_vars(monkeypatch):
    monkeypatch.setenv("TME_APP_TOKEN", "test-token")
    monkeypatch.setenv("TME_APP_SECRET", "test-secret")
    monkeypatch.setenv("TME_LANGUAGE", "EN")
    monkeypatch.setenv("TME_COUNTRY", "PL")

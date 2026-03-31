"""Shared fixtures for mcp-nexar tests."""

import json
import os
import sys
from pathlib import Path

import pytest

# Add the parent directory to sys.path so tests can import from mcp-nexar modules
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def search_response():
    """Load the example supSearch response fixture."""
    with open(FIXTURES_DIR / "search_response.json") as f:
        return json.load(f)


@pytest.fixture
def empty_search_response():
    """Load the empty search response fixture."""
    with open(FIXTURES_DIR / "empty_search_response.json") as f:
        return json.load(f)


@pytest.fixture
def token_response():
    """Load the example token response fixture."""
    with open(FIXTURES_DIR / "token_response.json") as f:
        return json.load(f)


@pytest.fixture
def graphql_error_response():
    """Load the GraphQL error response fixture."""
    with open(FIXTURES_DIR / "graphql_error_response.json") as f:
        return json.load(f)


@pytest.fixture
def env_vars(monkeypatch):
    """Set required environment variables for testing."""
    monkeypatch.setenv("NEXAR_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("NEXAR_CLIENT_SECRET", "test-client-secret")

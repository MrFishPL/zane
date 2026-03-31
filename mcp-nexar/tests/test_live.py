"""Live smoke tests against the real Nexar API.

Marked with @pytest.mark.slow. Skipped when credentials are missing.
Run with: pytest tests/test_live.py -m slow
"""

import os
import sys
from pathlib import Path

import pytest

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Skip entire module if credentials not available
_has_credentials = bool(
    os.environ.get("NEXAR_CLIENT_ID") and os.environ.get("NEXAR_CLIENT_SECRET")
)

pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(
        not _has_credentials,
        reason="NEXAR_CLIENT_ID and NEXAR_CLIENT_SECRET must be set",
    ),
]


@pytest.fixture
def live_client():
    """Create a NexarClient with real credentials."""
    from nexar_client import NexarClient

    return NexarClient(
        client_id=os.environ["NEXAR_CLIENT_ID"],
        client_secret=os.environ["NEXAR_CLIENT_SECRET"],
    )


class TestLiveAPI:
    @pytest.mark.asyncio
    async def test_search_parts_live(self, live_client):
        """Smoke test: search for a common resistor."""
        result = await live_client.search_parts("10k resistor 0402")

        assert result["hits"] > 0
        assert len(result["results"]) > 0

        part = result["results"][0]
        assert part["mpn"] is not None
        assert part["manufacturer"] is not None
        assert part["description"] is not None

    @pytest.mark.asyncio
    async def test_check_lifecycle_live(self, live_client):
        """Smoke test: check lifecycle of a well-known MPN."""
        result = await live_client.check_lifecycle("RC0402FR-0710KL")

        assert result["lifecycle"] in ("active", "nrnd", "obsolete", "unknown")
        assert result["mpn"] is not None

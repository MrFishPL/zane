"""OAuth2 client credentials flow for Nexar API."""

import time

import httpx
import structlog

log = structlog.get_logger()

TOKEN_URL = "https://identity.nexar.com/connect/token"


class NexarAuth:
    """Manages OAuth2 tokens for the Nexar API with automatic refresh."""

    def __init__(self, client_id: str, client_secret: str) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._token: str | None = None
        self._expires_at: float = 0.0

    @property
    def is_expired(self) -> bool:
        """Check if the current token is expired (with 60s safety margin)."""
        return time.time() >= (self._expires_at - 60)

    async def get_token(self) -> str:
        """Return a valid bearer token, refreshing if needed."""
        if self._token and not self.is_expired:
            return self._token

        log.info("nexar_auth.refreshing_token")
        start = time.monotonic()

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                TOKEN_URL,
                data={
                    "grant_type": "client_credentials",
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                },
            )

        duration_ms = round((time.monotonic() - start) * 1000)

        if response.status_code != 200:
            log.error(
                "nexar_auth.token_failed",
                status=response.status_code,
                body=response.text[:200],
                duration_ms=duration_ms,
            )
            raise RuntimeError(
                f"Nexar token request failed: {response.status_code} {response.text[:200]}"
            )

        data = response.json()
        self._token = data["access_token"]
        self._expires_at = time.time() + data.get("expires_in", 3600)

        log.info(
            "nexar_auth.token_acquired",
            expires_in=data.get("expires_in"),
            duration_ms=duration_ms,
        )
        return self._token

    async def get_headers(self) -> dict[str, str]:
        """Return authorization headers for API requests."""
        token = await self.get_token()
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

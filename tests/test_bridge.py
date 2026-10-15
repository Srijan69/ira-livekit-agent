"""Tests for the local bridge that the desktop dashboard talks to.

Nothing here touches the network or a real LiveKit project. Tokens are minted
with throwaway credentials and verified by decoding them, which is the only
check that matters: a token missing its agent dispatch produces a dashboard
that connects to an empty room and waits forever.
"""

from __future__ import annotations

import json

import jwt
import pytest
from aiohttp.test_utils import TestClient, TestServer

from bridge import SHARED_SECRET_HEADER, TOKEN_TTL, BridgeConfig, create_app

TEST_KEY = "devkey"
TEST_SECRET = "a-secret-long-enough-for-hs256-signing-abcdef"
TEST_URL = "wss://example.livekit.cloud"
TEST_TOKEN_SECRET = "bridge-shared-secret"


def _config(**overrides) -> BridgeConfig:
    defaults = {
        "livekit_url": TEST_URL,
        "api_key": TEST_KEY,
        "api_secret": TEST_SECRET,
        "agent_name": "my-agent",
        "shared_secret": TEST_TOKEN_SECRET,
    }
    defaults.update(overrides)
    return BridgeConfig(**defaults)


@pytest.fixture
async def client(request) -> TestClient:
    config = getattr(request, "param", None) or _config()
    server = TestServer(create_app(config))
    async with TestClient(server) as test_client:
        yield test_client


def _auth() -> dict[str, str]:
    return {SHARED_SECRET_HEADER: TEST_TOKEN_SECRET}


def _claims(token: str) -> dict:
    return jwt.decode(token, TEST_SECRET, algorithms=["HS256"])


class TestToken:
    async def test_mints_a_token_for_a_generated_room(self, client: TestClient):
        response = await client.post("/token", json={}, headers=_auth())
        assert response.status == 200

        body = await response.json()
        assert body["url"] == TEST_URL
        assert body["room"]
        assert body["token"]

    async def test_token_carries_the_agent_dispatch(self, client: TestClient):
        """The load-bearing assertion in this file.

        The agent registers with `agent_name="my-agent"`, which puts it on
        explicit dispatch: it does not join rooms on its own. The token embeds
        a RoomConfiguration naming it, so the dashboard's own join is what
        pulls her in. Without this claim everything still "works" -- the room
        connects, the badge goes green, and nobody ever answers.
        """
        response = await client.post("/token", json={}, headers=_auth())
        body = await response.json()

        claims = _claims(body["token"])
        agents = claims["roomConfig"]["agents"]
        assert [agent["agentName"] for agent in agents] == ["my-agent"]

    async def test_token_grants_exactly_what_the_dashboard_needs(
        self, client: TestClient
    ):
        response = await client.post("/token", json={}, headers=_auth())
        claims = _claims((await response.json())["token"])

        video = claims["video"]
        assert video["roomJoin"] is True
        assert video["canPublish"] is True
        assert video["canSubscribe"] is True
        # Data publishing carries the UI's text commands on the lk.chat topic.
        assert video["canPublishData"] is True
        # Nothing administrative: the dashboard has no business creating or
        # listing rooms, and a leaked token should not be able to.
        assert not video.get("roomAdmin")
        assert not video.get("roomCreate")
        assert not video.get("roomList")

    async def test_room_and_identity_round_trip(self, client: TestClient):
        response = await client.post(
            "/token", json={"room": "ira-fixed", "identity": "boss"}, headers=_auth()
        )
        body = await response.json()

        assert body["room"] == "ira-fixed"
        claims = _claims(body["token"])
        assert claims["sub"] == "boss"
        assert claims["video"]["room"] == "ira-fixed"

    async def test_generated_rooms_are_unique(self, client: TestClient):
        first = await (await client.post("/token", json={}, headers=_auth())).json()
        second = await (await client.post("/token", json={}, headers=_auth())).json()
        assert first["room"] != second["room"]

    async def test_token_expires(self, client: TestClient):
        response = await client.post("/token", json={}, headers=_auth())
        claims = _claims((await response.json())["token"])
        # The SDK emits `nbf` and `exp`, not `iat`. A token with no expiry is
        # a permanent key to IRA's room sitting in the renderer's memory.
        assert claims["exp"] - claims["nbf"] == int(TOKEN_TTL.total_seconds())

    async def test_rejects_an_oversized_room_name(self, client: TestClient):
        # Room names arrive over a loopback socket any local process can reach.
        response = await client.post(
            "/token", json={"room": "x" * 500}, headers=_auth()
        )
        assert response.status == 400

    async def test_never_returns_the_api_secret(self, client: TestClient):
        response = await client.post("/token", json={}, headers=_auth())
        assert TEST_SECRET not in await response.text()


class TestSharedSecret:
    """Loopback binding stops the network, not other local processes.

    A token grants the ability to join IRA's room and talk to her, which per
    the project's own security notes means issuing desktop commands. So the
    bridge requires a per-launch secret that only the Electron process that
    spawned it knows.
    """

    async def test_rejects_a_missing_secret(self, client: TestClient):
        response = await client.post("/token", json={})
        assert response.status == 401

    async def test_rejects_a_wrong_secret(self, client: TestClient):
        response = await client.post(
            "/token", json={}, headers={SHARED_SECRET_HEADER: "not-it"}
        )
        assert response.status == 401

    @pytest.mark.parametrize("client", [_config(shared_secret="")], indirect=True)
    async def test_refuses_to_run_unauthenticated(self, client: TestClient):
        """An empty secret must fail closed, not open.

        A misconfiguration that silently disables authentication is worse than
        one that stops the app starting.
        """
        response = await client.post("/token", json={})
        assert response.status == 401


class TestHealth:
    async def test_reports_configuration_without_a_secret(self, client: TestClient):
        # Health is reachable unauthenticated on purpose: its whole job is to
        # explain why nothing works, including a bad shared secret.
        response = await client.get("/health")
        assert response.status == 200

        body = await response.json()
        assert body["bridge"] == "ok"
        assert body["config"] == {
            "livekit_url": True,
            "api_key": True,
            "api_secret": True,
            "google_api_key": True,
        }

    @pytest.mark.parametrize(
        "client", [_config(livekit_url="", google_api_key="")], indirect=True
    )
    async def test_names_what_is_missing(self, client: TestClient):
        body = await (await client.get("/health")).json()
        assert body["config"]["livekit_url"] is False
        assert body["config"]["google_api_key"] is False
        assert body["config"]["api_key"] is True

    async def test_health_never_leaks_values(self, client: TestClient):
        """Booleans only.

        The point is to say whether a key is set, never what it is -- this
        endpoint is deliberately the one thing reachable without the secret.
        """
        text = await (await client.get("/health")).text()
        assert TEST_SECRET not in text
        assert TEST_KEY not in text
        assert set(json.loads(text)["config"].values()) <= {True, False}


class TestMisconfiguration:
    @pytest.mark.parametrize("client", [_config(api_key="")], indirect=True)
    async def test_token_fails_clearly_without_credentials(self, client: TestClient):
        response = await client.post("/token", json={}, headers=_auth())
        assert response.status == 503
        assert "LIVEKIT_API_KEY" in (await response.json())["error"]

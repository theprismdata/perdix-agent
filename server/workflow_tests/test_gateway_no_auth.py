from collections.abc import AsyncIterator
from pathlib import Path

import httpx2
import pytest
import pytest_asyncio
from pydantic import SecretStr, ValidationError

from perdix.config.schema import ServerConfig
from perdix.gateway.app import create_app
from perdix.gateway.config import GatewayConfig, GatewaySecrets, ProjectConfig

CLIENT = {"X-Perdix-Client": "dashboard"}


def make_config(tmp_path: Path, **extra: object) -> GatewayConfig:
    return GatewayConfig(
        db_path=tmp_path / "gateway.db",
        projects={"local": ProjectConfig(cwd=tmp_path)},
        server=ServerConfig(model="configured-test-model", db_path=str(tmp_path / "sessions.db")),
        **extra,
    )


@pytest_asyncio.fixture
async def client(tmp_path: Path) -> AsyncIterator[httpx2.AsyncClient]:
    app = create_app(make_config(tmp_path, auth="none"), GatewaySecrets())
    async with app.router.lifespan_context(app):
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app), base_url="http://testserver"
        ) as connection:
            yield connection


def test_no_auth_requires_loopback_host(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        make_config(tmp_path, auth="none", host="0.0.0.0")
    assert make_config(tmp_path, auth="none", host="localhost").auth == "none"


def test_short_gateway_token_is_still_rejected() -> None:
    with pytest.raises(ValidationError):
        GatewaySecrets(gateway=SecretStr("short"))


@pytest.mark.asyncio
async def test_dashboard_reads_without_token(client: httpx2.AsyncClient) -> None:
    response = await client.get("/api/status", headers=CLIENT)
    assert response.status_code == 200
    assert (await client.get("/api/jobs", headers=CLIENT)).json() == []


@pytest.mark.asyncio
async def test_request_without_dashboard_header_is_rejected(client: httpx2.AsyncClient) -> None:
    assert (await client.get("/api/status")).status_code == 403
    response = await client.post("/api/jobs", json={"project": "local", "prompt": "x"})
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_foreign_origin_is_rejected(client: httpx2.AsyncClient) -> None:
    response = await client.get("/api/status", headers={**CLIENT, "Origin": "https://evil.example"})
    assert response.status_code == 403
    local = await client.get("/api/status", headers={**CLIENT, "Origin": "http://127.0.0.1:18790"})
    assert local.status_code == 200


@pytest.mark.asyncio
async def test_unknown_host_header_is_rejected(client: httpx2.AsyncClient) -> None:
    response = await client.get("/api/status", headers={**CLIENT, "Host": "evil.example"})
    assert response.status_code == 400


def test_auth_defaults_to_none_on_loopback_and_token_elsewhere(tmp_path: Path) -> None:
    # 개인용 기본값: 루프백은 토큰 없이, 그 밖의 호스트는 토큰을 요구한다.
    assert make_config(tmp_path).auth == "none"
    assert make_config(tmp_path, host="localhost").auth == "none"
    assert make_config(tmp_path, host="0.0.0.0").auth == "token"
    assert make_config(tmp_path, auth="token").auth == "token"
    with pytest.raises(ValidationError):
        make_config(tmp_path, host="0.0.0.0", auth="none")

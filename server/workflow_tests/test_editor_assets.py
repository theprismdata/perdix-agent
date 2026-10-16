from collections.abc import AsyncIterator
from pathlib import Path

import httpx2
import pytest
import pytest_asyncio
from pydantic import SecretStr

from perdix.config.schema import ServerConfig
from perdix.gateway.app import create_app
from perdix.gateway.config import GatewayConfig, GatewaySecrets, ProjectConfig

TOKEN = "editor-api-fixture-connection-token"


@pytest_asyncio.fixture
async def client(tmp_path: Path) -> AsyncIterator[httpx2.AsyncClient]:
    config = GatewayConfig(
        db_path=tmp_path / "gateway.db",
        projects={"editor": ProjectConfig(cwd=tmp_path)},
        server=ServerConfig(model="configured-test-model", db_path=str(tmp_path / "sessions.db")),
    )
    app = create_app(config, GatewaySecrets(gateway=SecretStr(TOKEN)))
    async with app.router.lifespan_context(app):
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app), base_url="http://testserver"
        ) as connection:
            yield connection


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "asset", ["workflow-editor.js", "draft-plan.js", "draft-canvas.js", "editor.css"]
)
async def test_editor_modules_have_correct_mime_and_csp(
    client: httpx2.AsyncClient, asset: str
) -> None:
    response = await client.get("/" + asset)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith(
        "text/css" if asset.endswith(".css") else "text/javascript"
    )
    assert "default-src 'self'" in response.headers["content-security-policy"]
    assert response.content
    assert (await client.get("/not-an-editor-module.js")).status_code == 404


@pytest.mark.asyncio
async def test_editor_default_model_uses_authenticated_status(client: httpx2.AsyncClient) -> None:
    assert (await client.get("/api/status")).status_code == 401
    response = await client.get("/api/status", headers={"Authorization": "Bearer " + TOKEN})
    assert response.status_code == 200
    assert response.json()["workflowDefaultModel"] == "configured-test-model"

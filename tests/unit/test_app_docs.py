import httpx
import pytest

from payment_service.api.app import create_app
from payment_service.config import Settings

DOCS_PATHS = ["/docs", "/redoc", "/openapi.json"]


async def get(settings: Settings, path: str) -> httpx.Response:
    app = create_app(settings)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get(path)


@pytest.mark.parametrize("path", DOCS_PATHS)
async def test_docs_are_published_locally(settings: Settings, path: str) -> None:
    response = await get(settings, path)

    assert response.status_code == 200


@pytest.mark.parametrize("path", DOCS_PATHS)
async def test_docs_are_closed_in_production(settings: Settings, path: str) -> None:
    production = settings.model_copy(update={"environment": "production"})

    response = await get(production, path)

    assert response.status_code == 404

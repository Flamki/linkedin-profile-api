from datetime import UTC, datetime

from fastapi.testclient import TestClient
from pydantic import SecretStr

from linkedin_profile_api.app import create_app
from linkedin_profile_api.config import Settings
from linkedin_profile_api.models import Profile, ScrapeMeta, ScrapeResponse


class FakeScraper:
    async def close(self) -> None:
        pass

    async def scrape(self, profile_url: str) -> ScrapeResponse:
        return ScrapeResponse(
            data=Profile(profile_url=profile_url, name="Example Person", skills=["Python"]),
            meta=ScrapeMeta(retrieved_at=datetime.now(UTC), source="dom"),
        )


def make_client(api_key: str | None = None) -> TestClient:
    settings = Settings(
        _env_file=None,
        linkedin_li_at=SecretStr("test-cookie"),
        api_key=SecretStr(api_key) if api_key else None,
        rate_limit_per_minute=20,
    )
    return TestClient(create_app(settings=settings, scraper=FakeScraper()))


def test_health() -> None:
    with make_client() as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_scrape_endpoint() -> None:
    with make_client() as client:
        response = client.post(
            "/v1/profiles/scrape",
            json={"profile_url": "https://linkedin.com/in/example-person?trk=test"},
        )
    assert response.status_code == 200
    assert response.json()["data"]["profile_url"] == "https://www.linkedin.com/in/example-person/"
    assert response.json()["data"]["skills"] == ["Python"]
    assert response.headers["x-request-id"]


def test_rejects_invalid_host() -> None:
    with make_client() as client:
        response = client.post(
            "/v1/profiles/scrape", json={"profile_url": "https://example.com/in/person"}
        )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_optional_api_key() -> None:
    with make_client("secret") as client:
        unauthorized = client.post(
            "/v1/profiles/scrape", json={"profile_url": "https://linkedin.com/in/example"}
        )
        authorized = client.post(
            "/v1/profiles/scrape",
            headers={"X-API-Key": "secret"},
            json={"profile_url": "https://linkedin.com/in/example"},
        )
    assert unauthorized.status_code == 401
    assert authorized.status_code == 200


def test_missing_linkedin_cookie_returns_typed_service_error() -> None:
    settings = Settings(_env_file=None, linkedin_li_at=None)
    with TestClient(create_app(settings=settings)) as client:
        response = client.post(
            "/v1/profiles/scrape",
            json={"profile_url": "https://linkedin.com/in/example"},
        )
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "LINKEDIN_AUTH_REQUIRED"

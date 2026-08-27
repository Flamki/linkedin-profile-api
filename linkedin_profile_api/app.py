import asyncio
import hmac
import logging
import time
from collections import defaultdict, deque
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any, Protocol
from uuid import uuid4

from fastapi import Depends, FastAPI, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse

from linkedin_profile_api import __version__
from linkedin_profile_api.config import Settings, get_settings
from linkedin_profile_api.models import (
    ErrorResponse,
    HealthResponse,
    ScrapeRequest,
    ScrapeResponse,
)
from linkedin_profile_api.scraper import PlaywrightLinkedInScraper
from linkedin_profile_api.scraper.errors import (
    AuthenticationError,
    LinkedInBlockedError,
    ProfileNotFoundError,
    ScraperError,
    ScrapeTimeoutError,
)

logger = logging.getLogger(__name__)


class ScraperProtocol(Protocol):
    async def scrape(self, profile_url: str) -> ScrapeResponse: ...

    async def close(self) -> None: ...


class ApiKeyError(Exception):
    pass


class RateLimitError(Exception):
    pass


class InMemoryRateLimiter:
    def __init__(self, requests_per_minute: int) -> None:
        self.limit = requests_per_minute
        self._requests: defaultdict[str, deque[float]] = defaultdict(deque)
        self._lock = asyncio.Lock()

    async def allow(self, key: str) -> bool:
        now = time.monotonic()
        async with self._lock:
            bucket = self._requests[key]
            while bucket and now - bucket[0] >= 60:
                bucket.popleft()
            if len(bucket) >= self.limit:
                return False
            bucket.append(now)
            return True


def error_response(request: Request, status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={
            "error": {
                "code": code,
                "message": message,
                "request_id": getattr(request.state, "request_id", "unknown"),
            }
        },
    )


def create_app(
    *,
    settings: Settings | None = None,
    scraper: ScraperProtocol | None = None,
) -> FastAPI:
    app_settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        application.state.scraper = scraper or PlaywrightLinkedInScraper(app_settings)
        try:
            yield
        finally:
            await application.state.scraper.close()

    app = FastAPI(
        title=app_settings.app_name,
        version=__version__,
        description=(
            "Extracts the LinkedIn profile data visible to the configured authenticated account. "
            "Only submit profiles you are authorized to process."
        ),
        lifespan=lifespan,
        contact={"name": "Challenge submission"},
        license_info={"name": "MIT"},
    )
    app.state.settings = app_settings
    limiter = InMemoryRateLimiter(app_settings.rate_limit_per_minute)

    @app.middleware("http")
    async def request_context(request: Request, call_next: Callable[..., Any]) -> Any:
        request.state.request_id = request.headers.get("x-request-id", str(uuid4()))[:128]
        started = time.perf_counter()
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        logger.info(
            "%s %s -> %s in %.0fms request_id=%s",
            request.method,
            request.url.path,
            response.status_code,
            (time.perf_counter() - started) * 1000,
            request.state.request_id,
        )
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        message = "; ".join(
            f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
            for error in exc.errors()
        )
        return error_response(request, 422, "VALIDATION_ERROR", message)

    @app.exception_handler(AuthenticationError)
    async def authentication_error(request: Request, exc: AuthenticationError) -> JSONResponse:
        return error_response(request, 503, "LINKEDIN_AUTH_REQUIRED", str(exc))

    @app.exception_handler(ProfileNotFoundError)
    async def not_found_error(request: Request, exc: ProfileNotFoundError) -> JSONResponse:
        return error_response(request, 404, "PROFILE_NOT_FOUND", str(exc))

    @app.exception_handler(LinkedInBlockedError)
    async def blocked_error(request: Request, exc: LinkedInBlockedError) -> JSONResponse:
        return error_response(request, 503, "LINKEDIN_RATE_LIMITED", str(exc))

    @app.exception_handler(ScrapeTimeoutError)
    async def timeout_error(request: Request, exc: ScrapeTimeoutError) -> JSONResponse:
        return error_response(request, 504, "SCRAPE_TIMEOUT", str(exc))

    @app.exception_handler(ScraperError)
    async def scraper_error(request: Request, exc: ScraperError) -> JSONResponse:
        return error_response(request, 502, "SCRAPE_FAILED", str(exc))

    async def authorize(
        request: Request,
        x_api_key: str | None = Header(default=None),
    ) -> None:
        expected = app_settings.api_key
        if expected and (
            not x_api_key or not hmac.compare_digest(x_api_key, expected.get_secret_value())
        ):
            raise ApiKeyError
        client = request.client.host if request.client else "unknown"
        if not await limiter.allow(client):
            raise RateLimitError

    @app.get("/", include_in_schema=False)
    async def root() -> RedirectResponse:
        return RedirectResponse(url="/docs")

    @app.get("/health", response_model=HealthResponse, tags=["system"])
    async def health() -> HealthResponse:
        return HealthResponse(version=__version__)

    @app.post(
        "/v1/profiles/scrape",
        response_model=ScrapeResponse,
        responses={
            401: {"model": ErrorResponse},
            404: {"model": ErrorResponse},
            422: {"model": ErrorResponse},
            429: {"model": ErrorResponse},
            503: {"model": ErrorResponse},
            504: {"model": ErrorResponse},
        },
        tags=["profiles"],
        summary="Extract a LinkedIn profile",
        dependencies=[Depends(authorize)],
    )
    async def scrape_profile(payload: ScrapeRequest, request: Request) -> ScrapeResponse:
        result: ScrapeResponse = await request.app.state.scraper.scrape(payload.profile_url)
        return result

    @app.exception_handler(ApiKeyError)
    async def api_key_error(request: Request, exc: ApiKeyError) -> JSONResponse:
        return error_response(request, 401, "UNAUTHORIZED", "A valid X-API-Key header is required")

    @app.exception_handler(RateLimitError)
    async def rate_limit_error(request: Request, exc: RateLimitError) -> JSONResponse:
        response = error_response(request, 429, "RATE_LIMITED", "Try again in one minute")
        response.headers["Retry-After"] = "60"
        return response

    return app


app = create_app()

import asyncio
import contextlib
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

from playwright.async_api import Browser, BrowserContext, Page, Playwright, async_playwright

from linkedin_profile_api.config import Settings
from linkedin_profile_api.models import Profile, ProfileImages, ScrapeMeta, ScrapeResponse
from linkedin_profile_api.scraper.errors import (
    AuthenticationError,
    LinkedInBlockedError,
    ProfileNotFoundError,
    ScrapeTimeoutError,
)
from linkedin_profile_api.scraper.parser import (
    parse_certification_blocks,
    parse_education_blocks,
    parse_experience_blocks,
    parse_language_blocks,
    parse_voyager_payload,
    split_lines,
)


class PlaywrightLinkedInScraper:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._semaphore = asyncio.Semaphore(settings.max_concurrent_scrapes)

    async def start(self) -> None:
        if self._browser:
            return
        if not self.settings.linkedin_li_at:
            raise AuthenticationError("LINKEDIN_LI_AT is not configured")
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=self.settings.headless,
            args=["--disable-dev-shm-usage"],
        )

    async def close(self) -> None:
        if self._browser:
            await self._browser.close()
            self._browser = None
        if self._playwright:
            await self._playwright.stop()
            self._playwright = None

    async def scrape(self, profile_url: str) -> ScrapeResponse:
        async with self._semaphore:
            try:
                async with asyncio.timeout(self.settings.request_timeout_seconds):
                    return await self._scrape(profile_url)
            except TimeoutError as exc:
                raise ScrapeTimeoutError("LinkedIn did not respond before the timeout") from exc

    async def _scrape(self, profile_url: str) -> ScrapeResponse:
        if not self._browser:
            await self.start()
        assert self._browser is not None

        context = await self._new_context()
        warnings: list[str] = []
        try:
            page = await context.new_page()
            await self._navigate(page, profile_url)
            await self._assert_access(page)

            profile = Profile(profile_url=profile_url)
            voyager_used = False
            payload = await self._fetch_voyager(page, profile_url)
            if payload:
                candidate = parse_voyager_payload(payload, profile_url)
                if candidate.name or candidate.experience or candidate.education:
                    profile = candidate
                    voyager_used = True
                else:
                    warnings.append("Voyager returned data but no recognized profile entities")
            else:
                warnings.append("Voyager endpoint was unavailable; DOM extraction was used")

            dom_profile = await self._extract_dom(page, profile_url)
            profile = self._merge(profile, dom_profile)
            source = "voyager+dom" if voyager_used else "dom"

            if not profile.name:
                raise ProfileNotFoundError(
                    "The profile was unavailable or not visible to this account"
                )
            if not profile.experience:
                warnings.append("No visible experience entries were found")
            if not profile.education:
                warnings.append("No visible education entries were found")

            return ScrapeResponse(
                data=profile,
                meta=ScrapeMeta(
                    retrieved_at=datetime.now(UTC),
                    source=source,
                    warnings=warnings,
                ),
            )
        finally:
            await context.close()

    async def _new_context(self) -> BrowserContext:
        assert self._browser is not None
        context = await self._browser.new_context(
            locale="en-US",
            viewport={"width": 1440, "height": 1200},
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
            ),
        )
        cookies: list[dict[str, Any]] = [
            {
                "name": "li_at",
                "value": self.settings.linkedin_li_at.get_secret_value(),  # type: ignore[union-attr]
                "domain": ".linkedin.com",
                "path": "/",
                "httpOnly": True,
                "secure": True,
                "sameSite": "None",
            }
        ]
        if self.settings.linkedin_jsessionid:
            cookies.append(
                {
                    "name": "JSESSIONID",
                    "value": self.settings.linkedin_jsessionid.get_secret_value(),
                    "domain": ".linkedin.com",
                    "path": "/",
                    "httpOnly": False,
                    "secure": True,
                    "sameSite": "None",
                }
            )
        await context.add_cookies(cookies)  # type: ignore[arg-type]

        async def block_heavy_assets(route: Any) -> None:
            if route.request.resource_type in {"media", "font"}:
                await route.abort()
            else:
                await route.continue_()

        await context.route("**/*", block_heavy_assets)
        return context

    async def _navigate(self, page: Page, url: str) -> None:
        response = await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        if response and response.status == 404:
            raise ProfileNotFoundError("LinkedIn returned 404 for this profile")
        if response and response.status in {429, 999}:
            raise LinkedInBlockedError("LinkedIn rate-limited or blocked the session")
        with contextlib.suppress(Exception):
            await page.locator("main").first.wait_for(state="visible", timeout=10_000)

    async def _assert_access(self, page: Page) -> None:
        current = page.url.lower()
        if any(path in current for path in ("/login", "/checkpoint", "/authwall")):
            raise AuthenticationError(
                "LinkedIn cookie is expired or the account needs verification"
            )
        body = (await page.locator("body").inner_text()).lower()
        if "security verification" in body or "let's do a quick verification" in body:
            raise AuthenticationError("LinkedIn requested interactive verification")
        if "profile not found" in body or "this profile is not available" in body:
            raise ProfileNotFoundError("LinkedIn reports that this profile is unavailable")

    async def _fetch_voyager(self, page: Page, profile_url: str) -> dict[str, Any] | None:
        slug = profile_url.rstrip("/").rsplit("/", 1)[-1]
        endpoint = f"/voyager/api/identity/profiles/{quote(slug, safe='')}/profileView"
        result = await page.evaluate(
            """async (endpoint) => {
                const cookie = document.cookie
                    .split('; ')
                    .find(value => value.startsWith('JSESSIONID='));
                const csrf = cookie
                    ? decodeURIComponent(cookie.split('=').slice(1).join('=')).replaceAll('"', '')
                    : null;
                const headers = {
                    'accept': 'application/vnd.linkedin.normalized+json+2.1',
                    'x-restli-protocol-version': '2.0.0'
                };
                if (csrf) headers['csrf-token'] = csrf;
                const response = await fetch(endpoint, {
                    credentials: 'include',
                    headers
                });
                if (!response.ok) return {status: response.status, body: null};
                return {status: response.status, body: await response.json()};
            }""",
            endpoint,
        )
        if result.get("status") in {401, 403}:
            return None
        return result.get("body") if result.get("status") == 200 else None

    async def _extract_dom(self, page: Page, profile_url: str) -> Profile:
        overview = await page.evaluate(
            """() => {
                const text = (...selectors) => {
                    for (const selector of selectors) {
                        const node = document.querySelector(selector);
                        const value = node?.innerText?.trim();
                        if (value) return value;
                    }
                    return null;
                };
                const attr = (selector, name) =>
                    document.querySelector(selector)?.getAttribute(name) || null;
                const aboutAnchor = document.querySelector('#about');
                const aboutSection = aboutAnchor?.closest('section');
                const about = aboutSection
                    ?.querySelector('.inline-show-more-text')
                    ?.innerText?.trim() || null;
                return {
                    name: text('main h1', 'h1'),
                    headline: text(
                        '.text-body-medium.break-words',
                        'main [data-generated-suggestion-target]'
                    ),
                    location: text('main .text-body-small.inline.t-black--light.break-words'),
                    about,
                    profileImage:
                        attr('main img.pv-top-card-profile-picture__image--show', 'src') ||
                        attr('main img.pv-top-card-profile-picture__image', 'src'),
                    backgroundImage: attr('.profile-background-image__image-container img', 'src')
                };
            }"""
        )
        base = profile_url.rstrip("/")
        details: dict[str, list[str]] = {}
        for section in ("experience", "education", "skills", "certifications", "languages"):
            details[section] = await self._detail_blocks(page, f"{base}/details/{section}/")

        skills = [lines[0] for block in details["skills"] if (lines := split_lines(block))]
        return Profile(
            profile_url=profile_url,
            name=overview.get("name"),
            headline=overview.get("headline"),
            location=overview.get("location"),
            about=overview.get("about"),
            experience=parse_experience_blocks(details["experience"]),
            education=parse_education_blocks(details["education"]),
            skills=list(dict.fromkeys(skills)),
            certifications=parse_certification_blocks(details["certifications"]),
            languages=parse_language_blocks(details["languages"]),
            images=ProfileImages(
                profile=overview.get("profileImage"),
                background=overview.get("backgroundImage"),
            ),
        )

    async def _detail_blocks(self, page: Page, url: str) -> list[str]:
        try:
            await self._navigate(page, url)
            await self._assert_access(page)
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(500)
            result: Any = await page.evaluate(
                """() => {
                    const selectors = [
                        'main li.pvs-list__paged-list-item',
                        'main li.pvs-list__item--line-separated',
                        'main .pvs-list > li'
                    ];
                    for (const selector of selectors) {
                        const nodes = [...document.querySelectorAll(selector)];
                        const leaves = nodes.filter(node => !node.querySelector(selector));
                        const values = leaves.map(node => node.innerText.trim()).filter(Boolean);
                        if (values.length) return [...new Set(values)];
                    }
                    return [];
                }"""
            )
            return [str(item) for item in result] if isinstance(result, list) else []
        except (AuthenticationError, LinkedInBlockedError):
            raise
        except Exception:
            return []

    @staticmethod
    def _merge(primary: Profile, fallback: Profile) -> Profile:
        return Profile(
            profile_url=primary.profile_url,
            name=primary.name or fallback.name,
            headline=primary.headline or fallback.headline,
            location=primary.location or fallback.location,
            about=primary.about or fallback.about,
            experience=primary.experience or fallback.experience,
            education=primary.education or fallback.education,
            skills=primary.skills or fallback.skills,
            certifications=primary.certifications or fallback.certifications,
            languages=primary.languages or fallback.languages,
            images=ProfileImages(
                profile=primary.images.profile or fallback.images.profile,
                background=primary.images.background or fallback.images.background,
            ),
        )

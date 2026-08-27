class ScraperError(RuntimeError):
    """Base class for expected scraper failures."""


class AuthenticationError(ScraperError):
    pass


class ProfileNotFoundError(ScraperError):
    pass


class LinkedInBlockedError(ScraperError):
    pass


class ScrapeTimeoutError(ScraperError):
    pass

import pytest
from pydantic import ValidationError

from linkedin_profile_api.models import ScrapeRequest, canonicalize_linkedin_url


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (
            "https://linkedin.com/in/example-person?trk=public_profile",
            "https://www.linkedin.com/in/example-person/",
        ),
        (
            "https://uk.linkedin.com/in/example_123/",
            "https://www.linkedin.com/in/example_123/",
        ),
    ],
)
def test_canonicalizes_profile_urls(value: str, expected: str) -> None:
    assert canonicalize_linkedin_url(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        "http://www.linkedin.com/in/example",
        "https://evil.example/in/example",
        "https://linkedin.com.evil.example/in/example",
        "https://www.linkedin.com/company/openai",
        "https://user:pass@www.linkedin.com/in/example",
        "https://www.linkedin.com:444/in/example",
        "https://www.linkedin.com/in/a/extra",
    ],
)
def test_rejects_unsafe_or_non_profile_urls(value: str) -> None:
    with pytest.raises((ValueError, ValidationError)):
        ScrapeRequest(profile_url=value)

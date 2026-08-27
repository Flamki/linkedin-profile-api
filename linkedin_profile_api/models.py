import re
from datetime import datetime
from typing import Annotated, Literal
from urllib.parse import unquote, urlsplit

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

NonEmpty = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


def canonicalize_linkedin_url(value: str) -> str:
    """Validate a LinkedIn profile URL and return an SSRF-safe canonical URL."""
    try:
        parsed = urlsplit(value.strip())
    except ValueError as exc:
        raise ValueError("Invalid LinkedIn profile URL") from exc

    hostname = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme.lower() != "https":
        raise ValueError("Profile URL must use HTTPS")
    if parsed.username or parsed.password or parsed.port:
        raise ValueError("Profile URL must not contain credentials or a custom port")
    if hostname != "linkedin.com" and not hostname.endswith(".linkedin.com"):
        raise ValueError("Only linkedin.com profile URLs are accepted")

    segments = [unquote(part) for part in parsed.path.split("/") if part]
    if len(segments) != 2 or segments[0].lower() != "in":
        raise ValueError("Expected a LinkedIn URL in the form https://www.linkedin.com/in/username")
    slug = segments[1]
    if not re.fullmatch(r"[A-Za-z0-9_-]{2,100}", slug):
        raise ValueError("LinkedIn profile identifier contains unsupported characters")

    return f"https://www.linkedin.com/in/{slug}/"


class ScrapeRequest(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={"example": {"profile_url": "https://www.linkedin.com/in/satyanadella/"}}
    )

    profile_url: str

    @field_validator("profile_url")
    @classmethod
    def validate_profile_url(cls, value: str) -> str:
        return canonicalize_linkedin_url(value)


class DateRange(BaseModel):
    start: str | None = None
    end: str | None = None


class Experience(BaseModel):
    title: NonEmpty
    company: str | None = None
    employment_type: str | None = None
    location: str | None = None
    dates: DateRange = Field(default_factory=DateRange)
    duration: str | None = None
    description: str | None = None


class Education(BaseModel):
    school: NonEmpty
    degree: str | None = None
    field_of_study: str | None = None
    dates: DateRange = Field(default_factory=DateRange)
    description: str | None = None


class Certification(BaseModel):
    name: NonEmpty
    issuer: str | None = None
    issued: str | None = None
    expires: str | None = None
    credential_id: str | None = None
    credential_url: str | None = None


class Language(BaseModel):
    name: NonEmpty
    proficiency: str | None = None


class ProfileImages(BaseModel):
    profile: str | None = None
    background: str | None = None


class Profile(BaseModel):
    profile_url: str
    name: str | None = None
    headline: str | None = None
    location: str | None = None
    about: str | None = None
    experience: list[Experience] = Field(default_factory=list)
    education: list[Education] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    certifications: list[Certification] = Field(default_factory=list)
    languages: list[Language] = Field(default_factory=list)
    images: ProfileImages = Field(default_factory=ProfileImages)


class ScrapeMeta(BaseModel):
    retrieved_at: datetime
    source: Literal["voyager", "dom", "voyager+dom"]
    warnings: list[str] = Field(default_factory=list)


class ScrapeResponse(BaseModel):
    data: Profile
    meta: ScrapeMeta


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    version: str


class ErrorBody(BaseModel):
    code: str
    message: str
    request_id: str


class ErrorResponse(BaseModel):
    error: ErrorBody

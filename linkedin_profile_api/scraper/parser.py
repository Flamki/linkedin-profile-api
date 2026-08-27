import re
from collections.abc import Iterator
from typing import Any

from linkedin_profile_api.models import (
    Certification,
    DateRange,
    Education,
    Experience,
    Language,
    Profile,
    ProfileImages,
)


def _text(value: Any) -> str | None:
    if isinstance(value, str):
        cleaned = " ".join(value.split()).strip()
        return cleaned or None
    if isinstance(value, dict):
        for key in ("text", "name", "localizedName"):
            if result := _text(value.get(key)):
                return result
    return None


def _type(entity: dict[str, Any]) -> str:
    return str(entity.get("$type", entity.get("type", ""))).lower()


def _entities(value: Any) -> Iterator[dict[str, Any]]:
    if isinstance(value, dict):
        if "$type" in value or "type" in value:
            yield value
        for child in value.values():
            yield from _entities(child)
    elif isinstance(value, list):
        for child in value:
            yield from _entities(child)


def _date(value: Any) -> str | None:
    if not isinstance(value, dict):
        return _text(value)
    year = value.get("year")
    month = value.get("month")
    day = value.get("day")
    if not year:
        return None
    parts = [str(year)]
    if month:
        parts.append(f"{int(month):02d}")
    if day:
        parts.append(f"{int(day):02d}")
    return "-".join(parts)


def _period(entity: dict[str, Any]) -> DateRange:
    period = entity.get("timePeriod") or entity.get("dateRange") or {}
    if not isinstance(period, dict):
        return DateRange()
    return DateRange(start=_date(period.get("start")), end=_date(period.get("end")))


def _vector_image(value: Any) -> str | None:
    if not isinstance(value, dict):
        return _text(value)
    for nested_key in ("displayImageReference", "displayImage", "image"):
        nested = value.get(nested_key)
        if isinstance(nested, dict) and (result := _vector_image(nested)):
            return result
    vector_candidate = value.get("vectorImage")
    vector: dict[str, Any] = vector_candidate if isinstance(vector_candidate, dict) else value
    root = vector.get("rootUrl") or vector.get("rootUrlTemplate")
    artifacts = vector.get("artifacts") or []
    if root and artifacts:
        artifact = max(
            (item for item in artifacts if isinstance(item, dict)),
            key=lambda item: (item.get("width", 0) or 0) * (item.get("height", 0) or 0),
            default={},
        )
        segment = artifact.get("fileIdentifyingUrlPathSegment") or artifact.get("fileName")
        if segment:
            return f"{root}{segment}"
    return _text(value.get("url"))


def parse_voyager_payload(payload: dict[str, Any], profile_url: str) -> Profile:
    """Normalize both classic and Dash Voyager payloads into the public schema."""
    all_entities = list(_entities(payload))
    profile_entities = [
        item
        for item in all_entities
        if "profile" in _type(item)
        and not any(word in _type(item) for word in ("position", "view", "picture"))
    ]
    root = next(
        (
            item
            for item in profile_entities
            if item.get("firstName") or item.get("headline") or item.get("summary")
        ),
        {},
    )

    first = _text(root.get("firstName"))
    last = _text(root.get("lastName"))
    name = " ".join(part for part in (first, last) if part) or _text(root.get("name"))

    experience: list[Experience] = []
    education: list[Education] = []
    skills: list[str] = []
    certifications: list[Certification] = []
    languages: list[Language] = []

    seen: set[tuple[str, str]] = set()
    for entity in all_entities:
        kind = _type(entity)
        identity = str(entity.get("entityUrn") or entity.get("id") or id(entity))
        marker = (kind, identity)
        if marker in seen:
            continue
        seen.add(marker)

        if "position" in kind and "group" not in kind:
            title = _text(entity.get("title"))
            if title:
                company = _text(entity.get("companyName") or entity.get("company"))
                employment_type = _text(entity.get("employmentType"))
                experience.append(
                    Experience(
                        title=title,
                        company=company,
                        employment_type=employment_type,
                        location=_text(entity.get("locationName") or entity.get("location")),
                        dates=_period(entity),
                        description=_text(entity.get("description")),
                    )
                )
        elif "education" in kind:
            school = _text(entity.get("schoolName") or entity.get("school"))
            if school:
                education.append(
                    Education(
                        school=school,
                        degree=_text(entity.get("degreeName") or entity.get("degree")),
                        field_of_study=_text(
                            entity.get("fieldOfStudy") or entity.get("fieldOfStudyName")
                        ),
                        dates=_period(entity),
                        description=_text(entity.get("description") or entity.get("activities")),
                    )
                )
        elif re.search(r"(?:^|\.)skill$", kind):
            if skill := _text(entity.get("name")):
                skills.append(skill)
        elif "certification" in kind:
            cert_name = _text(entity.get("name"))
            if cert_name:
                period = _period(entity)
                certifications.append(
                    Certification(
                        name=cert_name,
                        issuer=_text(entity.get("authority") or entity.get("issuer")),
                        issued=period.start,
                        expires=period.end,
                        credential_id=_text(
                            entity.get("licenseNumber") or entity.get("credentialId")
                        ),
                        credential_url=_text(entity.get("url") or entity.get("credentialUrl")),
                    )
                )
        elif re.search(r"(?:^|\.)language$", kind):
            if language_name := _text(entity.get("name")):
                languages.append(
                    Language(
                        name=language_name,
                        proficiency=_text(entity.get("proficiency")),
                    )
                )

    picture = _vector_image(root.get("profilePicture") or root.get("picture"))
    background = _vector_image(root.get("backgroundPicture") or root.get("backgroundImage"))

    return Profile(
        profile_url=profile_url,
        name=name or None,
        headline=_text(root.get("headline")),
        location=_text(root.get("locationName") or root.get("geoLocationName")),
        about=_text(root.get("summary") or root.get("about")),
        experience=_dedupe(experience, lambda item: (item.title, item.company, item.dates.start)),
        education=_dedupe(education, lambda item: (item.school, item.degree, item.dates.start)),
        skills=list(dict.fromkeys(skills)),
        certifications=_dedupe(certifications, lambda item: (item.name, item.issuer)),
        languages=_dedupe(languages, lambda item: (item.name, item.proficiency)),
        images=ProfileImages(profile=picture, background=background),
    )


def _dedupe(items: list[Any], key: Any) -> list[Any]:
    result: list[Any] = []
    seen: set[Any] = set()
    for item in items:
        marker = key(item)
        if marker not in seen:
            seen.add(marker)
            result.append(item)
    return result


def split_lines(value: str) -> list[str]:
    ignored = {
        "show all",
        "see more",
        "show less",
        "see credential",
    }
    result: list[str] = []
    for raw in value.splitlines():
        line = " ".join(raw.split()).strip(" ·")
        if not line or line.lower() in ignored or (result and result[-1] == line):
            continue
        result.append(line)
    return result


_DATE_LINE = re.compile(
    r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec|Present|\b(?:19|20)\d{2}\b)",
    re.IGNORECASE,
)


def _display_dates(line: str) -> tuple[DateRange, str | None]:
    parts = [part.strip() for part in re.split(r"\s*[·•]\s*", line) if part.strip()]
    interval = parts[0] if parts else line
    duration = parts[1] if len(parts) > 1 else None
    bounds = [part.strip() for part in re.split(r"\s+[-–—]\s+", interval, maxsplit=1)]
    return DateRange(start=bounds[0] or None, end=bounds[1] if len(bounds) > 1 else None), duration


def parse_experience_blocks(blocks: list[str]) -> list[Experience]:
    result: list[Experience] = []
    for block in blocks:
        lines = split_lines(block)
        if not lines:
            continue
        date_index = next((i for i, line in enumerate(lines) if _DATE_LINE.search(line)), None)
        company_parts = re.split(r"\s*[·•]\s*", lines[1], maxsplit=1) if len(lines) > 1 else []
        dates, duration = (
            _display_dates(lines[date_index]) if date_index is not None else (DateRange(), None)
        )
        location_index = (
            date_index + 1 if date_index is not None and date_index + 1 < len(lines) else None
        )
        location = lines[location_index] if location_index is not None else None
        description_start = (
            (location_index + 1) if location_index is not None else (date_index or 1) + 1
        )
        result.append(
            Experience(
                title=lines[0],
                company=company_parts[0] if company_parts else None,
                employment_type=company_parts[1] if len(company_parts) > 1 else None,
                location=location,
                dates=dates,
                duration=duration,
                description="\n".join(lines[description_start:]) or None,
            )
        )
    return _dedupe(result, lambda item: (item.title, item.company, item.dates.start))


def parse_education_blocks(blocks: list[str]) -> list[Education]:
    result: list[Education] = []
    for block in blocks:
        lines = split_lines(block)
        if not lines:
            continue
        date_index = next((i for i, line in enumerate(lines) if _DATE_LINE.search(line)), None)
        dates = _display_dates(lines[date_index])[0] if date_index is not None else DateRange()
        degree_line = lines[1] if len(lines) > 1 and date_index != 1 else None
        degree_parts = [part.strip() for part in degree_line.split(",", 1)] if degree_line else []
        description_start = (date_index + 1) if date_index is not None else 2
        result.append(
            Education(
                school=lines[0],
                degree=degree_parts[0] if degree_parts else None,
                field_of_study=degree_parts[1] if len(degree_parts) > 1 else None,
                dates=dates,
                description="\n".join(lines[description_start:]) or None,
            )
        )
    return _dedupe(result, lambda item: (item.school, item.degree, item.dates.start))


def parse_certification_blocks(blocks: list[str]) -> list[Certification]:
    result: list[Certification] = []
    for block in blocks:
        lines = split_lines(block)
        if not lines:
            continue
        issued_line = next((line for line in lines if line.lower().startswith("issued ")), None)
        credential_line = next(
            (line for line in lines if line.lower().startswith("credential id")), None
        )
        issued = expires = None
        if issued_line:
            match = re.search(r"Issued\s+(.+?)(?:\s*[·•]\s*Expires\s+(.+))?$", issued_line, re.I)
            if match:
                issued, expires = match.group(1), match.group(2)
        result.append(
            Certification(
                name=lines[0],
                issuer=lines[1] if len(lines) > 1 and lines[1] != issued_line else None,
                issued=issued,
                expires=expires,
                credential_id=(credential_line.split(" ", 2)[-1] if credential_line else None),
            )
        )
    return _dedupe(result, lambda item: (item.name, item.issuer))


def parse_language_blocks(blocks: list[str]) -> list[Language]:
    items = [split_lines(block) for block in blocks]
    return _dedupe(
        [
            Language(name=lines[0], proficiency=lines[1] if len(lines) > 1 else None)
            for lines in items
            if lines
        ],
        lambda item: (item.name, item.proficiency),
    )

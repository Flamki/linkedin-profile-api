from linkedin_profile_api.scraper.parser import (
    parse_education_blocks,
    parse_experience_blocks,
    parse_voyager_payload,
)


def test_parses_normalized_voyager_payload() -> None:
    payload = {
        "data": {"*elements": ["urn:li:fsd_profile:abc"]},
        "included": [
            {
                "$type": "com.linkedin.voyager.dash.identity.profile.Profile",
                "entityUrn": "urn:li:fsd_profile:abc",
                "firstName": "Ada",
                "lastName": "Lovelace",
                "headline": "Computing pioneer",
                "geoLocationName": "London, United Kingdom",
                "summary": "I work on analytical engines.",
                "profilePicture": {
                    "displayImageReference": {
                        "vectorImage": {
                            "rootUrl": "https://media.example/",
                            "artifacts": [
                                {
                                    "width": 100,
                                    "height": 100,
                                    "fileIdentifyingUrlPathSegment": "small.jpg",
                                },
                                {
                                    "width": 800,
                                    "height": 800,
                                    "fileIdentifyingUrlPathSegment": "large.jpg",
                                },
                            ],
                        }
                    }
                },
            },
            {
                "$type": "com.linkedin.voyager.dash.identity.profile.Position",
                "entityUrn": "urn:li:fsd_position:1",
                "title": "Mathematician",
                "companyName": "Independent",
                "locationName": "London",
                "timePeriod": {"start": {"year": 1842}, "end": {"year": 1843}},
            },
            {
                "$type": "com.linkedin.voyager.dash.identity.profile.Education",
                "entityUrn": "urn:li:fsd_education:1",
                "schoolName": "University of London",
                "degreeName": "Mathematics",
            },
            {
                "$type": "com.linkedin.voyager.dash.identity.profile.Skill",
                "entityUrn": "urn:li:fsd_skill:1",
                "name": "Mathematics",
            },
        ],
    }

    profile = parse_voyager_payload(payload, "https://www.linkedin.com/in/ada/")

    assert profile.name == "Ada Lovelace"
    assert profile.headline == "Computing pioneer"
    assert profile.experience[0].company == "Independent"
    assert profile.experience[0].dates.start == "1842"
    assert profile.education[0].school == "University of London"
    assert profile.skills == ["Mathematics"]
    assert profile.images.profile == "https://media.example/large.jpg"


def test_parses_dom_experience_and_education() -> None:
    experience = parse_experience_blocks(
        [
            "Senior Engineer\nAcme · Full-time\nJan 2022 - Present · 2 yrs\n"
            "Bengaluru, India · Hybrid\nBuilt APIs"
        ]
    )
    education = parse_education_blocks(
        ["Example University\nB.Tech, Computer Science\n2017 - 2021\nGrade: A"]
    )

    assert experience[0].title == "Senior Engineer"
    assert experience[0].employment_type == "Full-time"
    assert experience[0].dates.end == "Present"
    assert education[0].degree == "B.Tech"
    assert education[0].field_of_study == "Computer Science"

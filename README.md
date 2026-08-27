# LinkedIn Profile API

A production-minded FastAPI service that accepts a LinkedIn profile URL and returns the
profile information visible to a configured LinkedIn account as structured JSON.

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/Flamki/linkedin-profile-api)

The extractor first calls LinkedIn's internal normalized Voyager profile-view endpoint from
an authenticated browser session. It normalizes the entity graph into a stable public schema,
then visits the profile detail routes to fill gaps from the rendered DOM. This hybrid approach
is more resilient than depending on either undocumented response shapes or CSS selectors alone.

> Use this project only for profiles you are authorized to process. Automated access can be
> restricted by LinkedIn's terms and may trigger account verification or rate limits.

## What is included

- Name, headline, location, about, experience, education, skills, certifications, languages,
  profile image, and background image when visible
- Strict LinkedIn URL validation and canonicalization (including SSRF protection)
- Optional API-key protection, per-process rate limiting, concurrency control, timeouts, and
  request IDs
- Interactive OpenAPI documentation at `/docs`
- Typed error responses, health check, unit/API tests, linting, type checking, CI, Docker, and a
  Render deployment blueprint
- No credentials, browser state, or profile data persisted to disk

## Architecture

```text
POST /v1/profiles/scrape
        |
        +-- validate/canonicalize linkedin.com/in/... URL
        +-- enforce API key, rate limit, concurrency, timeout
        +-- authenticated Playwright browser context (fresh per request)
        |      +-- Voyager normalized JSON -> typed entity parser
        |      +-- detail pages -> tolerant DOM parser/fallback
        +-- merge, deduplicate, validate -> stable JSON response
```

The browser process is shared to reduce startup cost; cookies and pages live in an isolated
context that is destroyed after each request. A single Uvicorn worker is deliberate because the
in-memory semaphore limits browser pressure and the rate limiter is process-local.

## Local setup

Prerequisites: Python 3.12+ and a LinkedIn account permitted to view the target profiles.

```bash
python -m venv .venv
# Windows PowerShell: .venv\Scripts\Activate.ps1
# macOS/Linux: source .venv/bin/activate
python -m pip install -e ".[dev]"
playwright install chromium
cp .env.example .env
```

On Windows, use `Copy-Item .env.example .env` instead of `cp` if preferred.

Set `LINKEDIN_LI_AT` in `.env` to the `li_at` cookie from an authenticated LinkedIn session.
In a browser where you are signed in, open developer tools, select **Application/Storage ->
Cookies -> https://www.linkedin.com**, and copy the value only. `LINKEDIN_JSESSIONID` is optional
but recommended. Never commit either value; they grant access to your session.

Start the API:

```bash
uvicorn linkedin_profile_api.app:app --reload --port 8000
```

Open `http://localhost:8000/docs` for the interactive API documentation.

## Docker

The image is based on Microsoft's version-matched Playwright image, so Chromium and its system
dependencies are already installed.

```bash
docker build -t linkedin-profile-api .
docker run --rm --init --ipc=host -p 8000:8000 --env-file .env linkedin-profile-api
```

## API

### `POST /v1/profiles/scrape`

If `API_KEY` is configured, send it as `X-API-Key`. Query strings and regional LinkedIn hosts are
accepted and canonicalized; only HTTPS `/in/{identifier}` profile URLs are allowed.

```bash
curl -X POST https://YOUR-HOST/v1/profiles/scrape \
  -H "Content-Type: application/json" \
  -H "X-API-Key: YOUR_API_KEY" \
  -d '{"profile_url":"https://www.linkedin.com/in/example-person/"}'
```

Representative response:

```json
{
  "data": {
    "profile_url": "https://www.linkedin.com/in/example-person/",
    "name": "Example Person",
    "headline": "Software Engineer",
    "location": "Bengaluru, Karnataka, India",
    "about": "I build reliable systems.",
    "experience": [
      {
        "title": "Senior Software Engineer",
        "company": "Example Co",
        "employment_type": "Full-time",
        "location": "Bengaluru, India",
        "dates": {"start": "2024-01", "end": null},
        "duration": null,
        "description": "Built internal APIs."
      }
    ],
    "education": [],
    "skills": ["Python", "Distributed Systems"],
    "certifications": [],
    "languages": [{"name": "English", "proficiency": "Full professional proficiency"}],
    "images": {
      "profile": "https://media.licdn.com/...",
      "background": null
    }
  },
  "meta": {
    "retrieved_at": "2026-08-27T10:00:00Z",
    "source": "voyager+dom",
    "warnings": []
  }
}
```

### Errors

Errors use one shape and include the same request ID returned in the `X-Request-ID` header:

```json
{
  "error": {
    "code": "PROFILE_NOT_FOUND",
    "message": "LinkedIn reports that this profile is unavailable",
    "request_id": "49c66051-25b3-4b82-9c29-b4421f9ac01f"
  }
}
```

Common status codes: `401` API key missing/invalid, `404` unavailable profile, `422` invalid input,
`429` client rate limit, `503` LinkedIn auth/rate limit, and `504` scrape timeout.

### `GET /health`

Unauthenticated liveness endpoint used by Docker and the hosting platform.

## Deploy over HTTPS

### Render blueprint

1. Push this repository to a public GitHub repository.
2. In Render, choose **New -> Blueprint** and select the repository. `render.yaml` configures the
   Docker service and health check.
3. Enter `LINKEDIN_LI_AT` and optionally `LINKEDIN_JSESSIONID` as secret environment variables.
   Render generates `API_KEY`; copy it once for the submission reviewer.
4. Wait for `/health` to return `200`, then make a test request through `/docs`.

Render terminates TLS at the edge, so the resulting `onrender.com` URL is HTTPS. The Starter plan
is specified because Playwright/Chromium generally exceeds small free-instance memory limits.

The same Dockerfile can be deployed to Cloud Run, Railway, Fly.io, or another container host.
Keep one application worker per container and scale horizontally only after moving rate-limit and
job state to Redis.

## Configuration

| Variable | Required | Default | Purpose |
| --- | --- | --- | --- |
| `LINKEDIN_LI_AT` | Yes | - | Authenticated LinkedIn session cookie |
| `LINKEDIN_JSESSIONID` | No | - | Voyager CSRF/session cookie |
| `API_KEY` | No | Open API | Requires matching `X-API-Key` when set |
| `HEADLESS` | No | `true` | Run Chromium without a visible window |
| `PORT` | No | `8000` | HTTP listen port |
| `REQUEST_TIMEOUT_SECONDS` | No | `60` | Whole-scrape timeout (10-180 seconds) |
| `MAX_CONCURRENT_SCRAPES` | No | `2` | Concurrent browser contexts per process |
| `RATE_LIMIT_PER_MINUTE` | No | `10` | Requests per client IP per process |
| `LOG_LEVEL` | No | `INFO` | Application log level |

## Quality checks

```bash
ruff check .
ruff format --check .
mypy linkedin_profile_api
pytest
docker build -t linkedin-profile-api .
```

CI runs the same static checks and test suite for every push and pull request.

## Known limitations and trade-offs

- LinkedIn's Voyager endpoints and DOM are undocumented and can change without notice. The two
  extraction paths reduce, but cannot remove, that maintenance risk.
- Results are limited to what the configured account can see. Private, removed, blocked, or
  out-of-network details may be absent.
- Session cookies expire and may trigger a checkpoint. Rotate the hosting secret without changing
  source code.
- LinkedIn may rate-limit automation. The service intentionally avoids CAPTCHA bypasses, proxies,
  fingerprint spoofing, and automatic credential login.
- Profile extraction is synchronous and browser-backed. For heavier traffic, return job IDs, use a
  durable queue, move limits to Redis, and run a controlled worker pool.
- The included limiter is per process and trusts the hosting proxy's client address. It is adequate
  for a single-container challenge deployment, not a distributed public service.
- Browser-driven integration tests require a real account and are intentionally excluded from CI;
  unit tests cover URL safety, response behavior, and both response parsers without using LinkedIn.

## Submission checklist

- [ ] Run every command under **Quality checks**.
- [ ] Push to a public GitHub repository; confirm `.env` and cookies are absent from commit history.
- [ ] Deploy the Docker service and confirm the public HTTPS `/health` and `/docs` endpoints.
- [ ] Test one authorized profile and inspect every returned section.
- [ ] Submit the repository URL, deployment URL, and reviewer API key through the challenge form.

## License

MIT. LinkedIn is a trademark of LinkedIn Corporation; this project is not affiliated with or
endorsed by LinkedIn.

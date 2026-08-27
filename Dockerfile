FROM mcr.microsoft.com/playwright/python:v1.62.0-noble

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8000

WORKDIR /app

COPY pyproject.toml README.md ./
COPY linkedin_profile_api ./linkedin_profile_api

RUN python -m pip install --upgrade pip && python -m pip install .

RUN useradd --create-home --uid 10001 apiuser && chown -R apiuser:apiuser /app
USER apiuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','8000')+'/health', timeout=3)"

CMD ["sh", "-c", "uvicorn linkedin_profile_api.app:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1 --proxy-headers"]

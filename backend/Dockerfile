# Transit API. Built and run by deploy/docker-compose.prod.yml (see docs/DEPLOY.md).
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /srv/transit

# Dependencies first, so a code change doesn't reinstall them.
COPY pyproject.toml ./
RUN python -c "import tomllib; print('\n'.join(tomllib.load(open('pyproject.toml', 'rb'))['project']['dependencies']))" \
      > requirements.txt \
 && pip install -r requirements.txt

COPY app ./app
COPY alembic.ini ./
COPY alembic ./alembic
COPY scripts ./scripts
RUN pip install --no-deps . \
 && useradd --system --uid 10001 --home-dir /srv/transit transit \
 && chown -R transit /srv/transit
USER transit

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD ["python", "-c", "import urllib.request, sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status == 200 else 1)"]

# ONE worker: the WebSocket hub, the event bus and the background jobs live in this process.
# A second worker or replica would run every job twice and miss pushes (docs/DEPLOY.md).
# Proxy headers are trusted from any address because only Caddy can reach this port: it is
# never published.
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1 --proxy-headers --forwarded-allow-ips=* --no-server-header"]

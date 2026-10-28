# The voice server as one container: FastAPI under uvicorn, holding long-lived WebSockets.
# Build and run from voice/:
#
#   docker build -t wren-voice .
#   docker run --env-file .env -p 8000:8000 wren-voice
#
# Keys come from the host's environment at run time. None are baked into the image.

FROM python:3.14.6-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app ./app
# voice/logs/turns.jsonl lives here. It's lost on redeploy; the database keeps the real record.
RUN useradd --create-home --uid 1000 wren && mkdir logs && chown wren logs
USER wren

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s \
    CMD python -c "import os, urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\", \"8000\")}/health', timeout=4)"

# One process: live conversations sit in its memory, so don't add workers or replicas.
# Hosts set PORT. WREN_DEV_USER_ID is cleared because it's for local development only: a
# proxy inside the host could make every caller look like loopback (see the voice-loop
# design, "Keys and access"). It's set to empty, not unset: load_dotenv fills a variable
# that's unset, so a .env that ever reached the image would bring the dev identity back,
# but it never overwrites one that's set, and an empty one means no dev identity.
CMD ["sh", "-c", "export WREN_DEV_USER_ID=; exec uvicorn app.main:app --host 0.0.0.0 --port \"${PORT:-8000}\""]

# Test Telegram bot of the UGMK-Zdorovye trauma centre.
#   docker compose up -d --build        (docker-compose.yml; on the VM — scripts/deploy.sh)
#   docker build -t ugmk-bot . && docker run -d --name ugmk-bot --env-file .env \
#       -v "$PWD/data:/app/data" ugmk-bot
# Secrets come from the env file at run time and never enter the image.

FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.11.20 /uv /usr/local/bin/uv

ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    TZ=Asia/Yekaterinburg

WORKDIR /app

# Dependencies first, so code changes do not reinstall them.
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev --no-install-project

# Runtime files only: no tests, no kb/text (clinic sources, used by tests), no PDFs.
COPY app ./app
COPY config ./config
COPY kb/scenarios ./kb/scenarios
COPY kb/info ./kb/info
# The welcome animation and the other media (assets/README.md): without them the messages
# go as text alone.
COPY assets ./assets

# data/ is empty in the image: the bot builds the demo schedule at start (app/schedule).
# The media are readable by the bot whatever mode the files had where the image was built.
RUN useradd --create-home --uid 1000 bot && mkdir -p /app/data && chown -R bot:bot /app/data \
    && chmod -R a+rX /app/assets
USER bot

# bot.db, llm_usage.jsonl and the demo schedule live here; mount it to keep them.
VOLUME ["/app/data"]

CMD ["/app/.venv/bin/python", "-m", "app"]

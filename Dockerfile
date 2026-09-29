# базовый образ можно подменить на зеркало: BASE_IMAGE=mirror.gcr.io/library/python:3.12-slim
ARG BASE_IMAGE=python:3.12-slim
FROM ${BASE_IMAGE}

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

RUN useradd --create-home --uid 1000 bot
WORKDIR /opt/azbuka_ssm

COPY pyproject.toml ./
COPY app ./app
COPY prompts ./prompts
RUN pip install --no-cache-dir .

RUN mkdir -p /data && chown bot:bot /data
USER bot
ENV DATA_DIR=/data PROMPT_PATH=/opt/azbuka_ssm/prompts/system.md

CMD ["python", "-m", "app.main"]

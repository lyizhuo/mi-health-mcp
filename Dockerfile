# syntax=docker/dockerfile:1
FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    MI_HEALTH_DATA_DIR=/data \
    MI_HEALTH_HOST=0.0.0.0

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install .

RUN useradd --create-home --uid 10001 app \
    && mkdir -p /data \
    && chown -R app:app /data

COPY docker/entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod 0755 /usr/local/bin/docker-entrypoint.sh

USER app
VOLUME ["/data"]
EXPOSE 8330
ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
CMD ["all"]

# syntax=docker/dockerfile:1.7
# Application image `collector` (§7.5, §13, FR-030): один image для api, scheduler, controller,
# CLI і worker roles — різні `command` у docker-compose.yml. Browser worker — окремий image
# (WP-02 PR3). GUI — окремий image (WP-00 PR3).
#
# Multi-stage: builder (python:3.13-slim + uv) → runtime (той самий python:3.13-slim, без uv,
# без build-залежностей). Обидва base images pinned tag + digest (multi-arch index digest).
# Оновлення digest: `docker buildx imagetools inspect python:3.13-slim` (поле Digest).
#
# Секретів у build немає і не повинно бути: жодного `ARG`/`ENV` з credentials, `.dockerignore`
# виключає `.env*` і `deploy/compose/secrets/`; runtime-секрети — Docker secrets/files (§7.5).

ARG PYTHON_IMAGE=python:3.13-slim@sha256:8d9d0b8bcf6506481eae4907c18f5e3e7902e629f5f6d684f9e7c32e85e3ddf0
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.12.13@sha256:b485bd65cc2cf1c9a93b3554012c9c3778cf7b1b5fd3d3096ce9e1226c97e1e6

FROM ${UV_IMAGE} AS uv

# ---------------------------------------------------------------------------------------------
FROM ${PYTHON_IMAGE} AS builder

COPY --from=uv /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/collector

WORKDIR /build

# 1) лише залежності (кеш шару не інвалідовується зміною src/), строго за uv.lock
COPY pyproject.toml uv.lock .python-version ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

# 2) сам пакет (non-editable, у site-packages venv)
COPY README.md ./
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable

# ---------------------------------------------------------------------------------------------
FROM ${PYTHON_IMAGE} AS runtime

ARG COLLECTOR_GIT_SHA=unknown
ARG COLLECTOR_SCHEMA_VERSION=0.0.0-placeholder
ARG COLLECTOR_VERSION=0.1.0
ARG COLLECTOR_CREATED=1970-01-01T00:00:00Z

# OCI labels (§7.5): Git SHA / schema version / джерело. Значення передає CI через --build-arg.
LABEL org.opencontainers.image.title="collector" \
      org.opencontainers.image.description="UA Web Data Collector — application image (api, scheduler, workers, CLI)" \
      org.opencontainers.image.version="${COLLECTOR_VERSION}" \
      org.opencontainers.image.revision="${COLLECTOR_GIT_SHA}" \
      org.opencontainers.image.created="${COLLECTOR_CREATED}" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.base.name="docker.io/library/python:3.13-slim" \
      ua.collector.schema-version="${COLLECTOR_SCHEMA_VERSION}"

# Мінімальні runtime-пакети: tini не потрібен (CLI ставить SIGTERM handler сам; Compose має
# `init: true` для zombie-reaping). Оновлення безпеки base image — через оновлення digest.
# pip і ensurepip-бандли з base image видаляються: venv зібрано uv, у runtime установка пакетів
# не потрібна (read-only rootfs), а vendored-пакети pip лише додають CVE у scan.
RUN groupadd --gid 10001 collector \
    && useradd --uid 10001 --gid 10001 --no-create-home --home-dir /nonexistent \
       --shell /usr/sbin/nologin collector \
    && rm -rf /usr/local/lib/python3.13/site-packages/pip* /usr/local/bin/pip* \
              /usr/local/lib/python3.13/ensurepip \
    && mkdir -p -m 0755 /app/config

COPY --from=builder --chown=root:root /opt/collector /opt/collector

# Реєстр джерел (WP-01C, approved dependency): контракти валідують `source_id` проти
# docs/research/source-registry.yaml через env COLLECTOR_SOURCE_REGISTRY. Файл read-only,
# належить root, --chmod=0644 (незалежно від прав у build-контексті, напр. Windows 0755); каталог
# /app/config створено вище з 0755 (COPY --chmod поширюється й на створювані каталоги — 0644
# зробив би каталог непрохідним). Читається non-root процесом, rootfs read-only.
COPY --chown=root:root --chmod=0644 docs/research/source-registry.yaml /app/config/source-registry.yaml

# TMPDIR/HOME → /tmp: tmpfs у Compose; read-only rootfs (§7.5) — усі тимчасові файли лише тут.
ENV PATH="/opt/collector/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUTF8=1 \
    COLLECTOR_GIT_SHA="${COLLECTOR_GIT_SHA}" \
    COLLECTOR_SOURCE_REGISTRY=/app/config/source-registry.yaml \
    TMPDIR=/tmp \
    HOME=/tmp

WORKDIR /app
USER 10001:10001

# Типовий healthcheck — процес/venv живі (CLI імпортується). Compose перевизначає
# per-service healthcheck «process + критична dependency» (§7.5).
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["collector", "version"]

ENTRYPOINT []
CMD ["collector", "--help"]

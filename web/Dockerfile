# syntax=docker/dockerfile:1.7
# Image `collector-gui` (§7.7, §8, §13): node builder → nginxinc/nginx-unprivileged.
#
# Обидва base images pinned tag + digest (multi-arch index digest), як і `collector`.
# Оновлення digest: `docker buildx imagetools inspect <tag>` (поле Digest).
#
# Build-контекст — `web/` (див. web/.dockerignore); конфіг nginx приходить окремим
# build-контекстом `gui-conf` (deploy/compose/gui/), бо він належить deploy-шару, а не
# застосунку. У образі немає ні Node, ні вихідників, ні node_modules — лише статика.
#
# Секретів у build немає: `npm ci` ставить лише публічні пакети за package-lock.json,
# жодного ARG/ENV з credentials. Runtime-конфіг (адреса api) — у nginx.conf, не у bundle.

ARG NODE_IMAGE=node:24.19-alpine@sha256:d32cdf619f63fe0471182d08996dd516c6275bb5fd31ae06e55a570bd9e1ad43
ARG NGINX_IMAGE=nginxinc/nginx-unprivileged:1.29-alpine@sha256:0c79d56aee561a1d81c63f00eee5fb5fe29279560cdc55e91425133104c7fbe6

# ---------------------------------------------------------------------------------------------
FROM ${NODE_IMAGE} AS builder

WORKDIR /build

# 1) лише маніфести — шар кешується, доки не змінився lockfile
COPY package.json package-lock.json ./
RUN --mount=type=cache,target=/root/.npm \
    npm ci --no-audit --no-fund

# 2) вихідники і конфіги збірки
# Усі tsconfig-и солюшену, включно з `tsconfig.test.json`: `vite build` читає кореневий
# `tsconfig.json` і резолвить його `references`, тому відсутній файл ламає збірку (самі
# тестові вихідники при цьому не потрібні — читається лише конфіг).
COPY tsconfig.json tsconfig.app.json tsconfig.node.json tsconfig.test.json ./
COPY vite.config.ts index.html ./
COPY src ./src

# `build:image` = `tsc -b tsconfig.app.json` (strict type check коду застосунку) + `vite build`:
# типова помилка ламає image, а не проявляється у рантаймі. Саме `build:image`, а не `build`:
# останній перевіряє ще й проєкти `tsconfig.test.json`/`tsconfig.node.json` (unit-тести,
# конфіги, Playwright), яких у build-контексті образу свідомо немає — вони не постачаються.
# Повний `npm run build` виконують розробник і CI job `web` (§16.2).
RUN npm run build:image

# ---------------------------------------------------------------------------------------------
FROM ${NGINX_IMAGE} AS runtime

ARG COLLECTOR_GIT_SHA=unknown
ARG COLLECTOR_VERSION=0.1.0
ARG COLLECTOR_CREATED=1970-01-01T00:00:00Z

# Alpine-пакети з базового образу відстають від security-оновлень дистрибутиву: локальний
# скан (`docker scout cves`, 2026-09-22) на pinned digest дав 13 CRITICAL / 41 HIGH, з них 53
# з доступним фіксом — майже все у `curl` і `openssl`. Новішого digest для тега
# `1.29-alpine` немає (перевірено `docker buildx imagetools inspect`), тому оновлюємо пакети
# самі: інакше trivy-крок CI (CRITICAL, без `ignore-unfixed` — §13) блокував би кожен PR, а
# єдиний публічний сервіс стека їздив би з відомими критичними CVE.
# Base image лишається pinned by digest (вимога картки); змінюється лише набір apk-пакетів,
# тому збірка перестає бути побайтово відтворюваною у часі — свідомий обмін на §13
# «critical CVE блокує release». `USER root` потрібен для apk; далі повертаємось на 101:101.
USER root
RUN apk upgrade --no-cache && rm -rf /var/cache/apk/*

LABEL org.opencontainers.image.title="collector-gui" \
      org.opencontainers.image.description="UA Web Data Collector — operator GUI (React SPA + nginx reverse proxy)" \
      org.opencontainers.image.version="${COLLECTOR_VERSION}" \
      org.opencontainers.image.revision="${COLLECTOR_GIT_SHA}" \
      org.opencontainers.image.created="${COLLECTOR_CREATED}" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.base.name="docker.io/nginxinc/nginx-unprivileged:1.29-alpine"

# Дефолтний server-блок образу замінюється нашим (CSP, security headers, /api proxy).
COPY --from=gui-conf --chown=root:root --chmod=0644 nginx.conf /etc/nginx/conf.d/default.conf
# Без --chmod: він поширився б і на каталоги (0644 робить їх непрохідними).
COPY --from=builder --chown=root:root /build/dist /usr/share/nginx/html

# Образ nginx-unprivileged уже перемикається на 101:101 і слухає 8080; фіксуємо явно, щоб
# `docker inspect --format '{{.Config.User}}'` мав значення і в CI-перевірці non-root.
USER 101:101
EXPOSE 8080

# Процес живий і віддає SPA. Залежність від api перевіряє healthcheck у docker-compose.yml.
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD ["wget", "--quiet", "--spider", "http://127.0.0.1:8080/healthz"]

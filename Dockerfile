# Base images are pinned by digest (the tag is kept for readability); Dependabot updates both.

# The web app: plain JavaScript and CSS, so it is built once on the build machine's platform.
FROM --platform=$BUILDPLATFORM node:26-alpine@sha256:6bd9ab43c0ec0190ca7b2d193906cb8b7edf6e7f4afa43ddeccd6452710bffb5 AS frontend
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# Static builds of ncdu and gdu for the target platform, pinned by version and SHA-256.
FROM --platform=$BUILDPLATFORM node:26-alpine@sha256:6bd9ab43c0ec0190ca7b2d193906cb8b7edf6e7f4afa43ddeccd6452710bffb5 AS engines
ARG TARGETARCH
ARG NCDU_BASE_URL=""
COPY scripts/install-engines.sh /install-engines.sh
RUN NCDU_BASE_URL="${NCDU_BASE_URL:-https://dev.yorhel.nl/download}" sh /install-engines.sh /engines "${TARGETARCH:-amd64}"

FROM ghcr.io/astral-sh/uv:0.13.8@sha256:5cc1805e2426484abca4ad46ea783bab281fdb225fd82d8ce6d66a81254bac85 AS uv

FROM python:3.15-slim@sha256:65a65fc7b30b06cb6df4930fd4650697cb23f4c2698ab81fc87e4b031bc36928 AS runtime
ENV TZ=Etc/UTC \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_NO_CACHE=1 \
    UV_PYTHON_DOWNLOADS=never \
    PATH="/app/.venv/bin:$PATH"
# The application runs from /app/.venv: the system pip is never used, and removing it also drops
# the outdated packages it vendors.
RUN python -m pip uninstall --yes --root-user-action=ignore pip \
    && groupadd --gid 1000 app && useradd --uid 1000 --gid app --create-home app
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN --mount=from=uv,source=/uv,target=/usr/local/bin/uv \
    uv sync --locked --no-dev --no-install-project
COPY README.md LICENSE ./
# The licenses of ncdu, gdu and the rest of the third-party software in the image.
COPY licenses/ ./licenses/
COPY src/ ./src/
# Building the project leaves build/ and an .egg-info in the source tree; the venv has the copy.
RUN --mount=from=uv,source=/uv,target=/usr/local/bin/uv \
    uv sync --locked --no-dev --no-editable \
    && rm -rf build src/*.egg-info
COPY --from=engines /engines/ /usr/local/bin/
COPY alembic.ini docker-entrypoint.sh ./
COPY --from=frontend /frontend/dist ./frontend/dist
# The engines must run on this platform; du (GNU coreutils) and ionice (util-linux) come with Debian.
RUN mkdir -p /app/data /data && chown app:app /app/data \
    && sed -i 's/\r$//' docker-entrypoint.sh && chmod +x docker-entrypoint.sh \
    && ncdu --version && gdu --version | head -1 && du --version | head -1 && ionice --version
ARG IMAGE_TAG=development
ARG BUILD_DATE=""
ENV IMAGE_TAG=$IMAGE_TAG BUILD_DATE=$BUILD_DATE
LABEL org.opencontainers.image.title="DiskSprawl" \
      org.opencontainers.image.description="Self-hosted disk usage analyzer: scheduled, gentle scans of your folders and network shares, with a treemap, history and what changed between scans" \
      org.opencontainers.image.source="https://github.com/DrDonoso/disksprawl" \
      org.opencontainers.image.url="https://github.com/DrDonoso/disksprawl" \
      org.opencontainers.image.documentation="https://github.com/DrDonoso/disksprawl#readme" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.version="$IMAGE_TAG" \
      org.opencontainers.image.created="$BUILD_DATE"
USER app
EXPOSE 7780
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --start-interval=2s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:7780/health', timeout=4)"
ENTRYPOINT ["./docker-entrypoint.sh"]

# The Tidewatch lab "patient": a fake agent app. Lab only, never pushed to a registry.
# Base images are pinned by digest (resolved 2026-11-01 with `docker buildx imagetools inspect`),
# so every build starts from exactly the same layers.
FROM ghcr.io/astral-sh/uv:0.12.23@sha256:61d393e44e249f2e4b526b6c7ddcecce245946826e608e11c93ad4f5bba55b21 AS uv

FROM python:3.12-slim@sha256:aa943ce411423c7582288f18707ff66eddcbaeb510502d747a72aeb34f30d159
COPY --from=uv /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONUNBUFFERED=1

WORKDIR /app
# Dependencies first (cached layer), then the app itself. No dev/test tools in the image.
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN uv sync --frozen --no-dev

# A numeric user, so Kubernetes can verify runAsNonRoot (a named user can't be checked).
USER 10001:10001
EXPOSE 8000
CMD ["/app/.venv/bin/tidewatch-agent"]

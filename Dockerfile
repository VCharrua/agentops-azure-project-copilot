# syntax=docker/dockerfile:1

# ---- build stage: resolve and install dependencies into a virtualenv -------------------------------
FROM python:3.15-slim AS build

ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
ARG DEPENDENCY_REFRESH
RUN apt-get update \
    && apt-get upgrade -y --no-install-recommends \
    && rm -rf /var/lib/apt/lists/* \
    && echo "Dependency refresh: ${DEPENDENCY_REFRESH}"
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

WORKDIR /src
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --upgrade pip setuptools msgpack urllib3 \
    && pip install ".[azure]"

# ---- runtime stage: no build tools, non-root user ---------------------------------------------------
FROM python:3.15-slim AS runtime

ARG APP_VERSION=dev
ARG DEPENDENCY_REFRESH
LABEL org.opencontainers.image.title="project-delivery-copilot" \
      org.opencontainers.image.description="Project Delivery Copilot API (synthetic data reference implementation)" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.version="${APP_VERSION}"

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    COPILOT_APP_VERSION=${APP_VERSION} \
    COPILOT_DATA_DIR=/app/data/synthetic \
    COPILOT_STATE_DIR=/tmp/copilot-state

RUN apt-get update \
    && apt-get upgrade -y --no-install-recommends \
    && rm -rf /var/lib/apt/lists/* \
    && /usr/local/bin/python -m pip uninstall --yes setuptools msgpack urllib3 \
    && echo "Dependency refresh: ${DEPENDENCY_REFRESH}"
RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin copilot
WORKDIR /app
COPY --from=build /opt/venv /opt/venv
# The synthetic dataset ships in the image: structured data is read from it in every mode, and the
# documents back the local retriever when COPILOT_MODE=local.
COPY data/synthetic ./data/synthetic

USER 10001
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
  CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2).status == 200 else 1)"]

CMD ["uvicorn", "copilot.api.main:app", "--host", "0.0.0.0", "--port", "8000"]

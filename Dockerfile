ARG PYTHON_BASE_IMAGE
# Production image must be built from a pre-reviewed immutable image digest.
# Supply --build-arg PYTHON_BASE_IMAGE=python:3.12-slim@sha256:<real digest>.
FROM ${PYTHON_BASE_IMAGE}
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 CONCIERGE_WEB_DIR=/app/web LANGGRAPH_STRICT_MSGPACK=true
WORKDIR /app
RUN useradd -u 10001 --create-home concierge
# Production dependencies come from an OPERATOR-APPROVED BuildKit context.
# No network dependency resolution, unpinned transitive packages or test fixtures.
COPY --from=python_deps /requirements.lock /tmp/requirements.lock
COPY --from=python_deps /wheels /tmp/wheels
RUN python -m pip install --no-index --no-cache-dir --require-hashes --ignore-installed \
    --find-links=/tmp/wheels -r /tmp/requirements.lock
COPY pyproject.toml ./
COPY src ./src
COPY tools ./tools
COPY config ./config
COPY web ./web
# Schema-only deployment. Never copy test fixtures or synthetic hotel knowledge.
COPY datasets/map ./datasets/map
COPY datasets/planning ./datasets/planning
RUN python -m pip install --no-index --no-deps --no-build-isolation . && \
    python -m pip check && \
    rm -rf /tmp/wheels /tmp/requirements.lock && \
    mkdir -p /data && chown concierge:concierge /data
USER concierge
EXPOSE 8000
CMD ["uvicorn", "concierge_kiosk.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--no-proxy-headers"]

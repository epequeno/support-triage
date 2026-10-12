# support-triage: the app as one container, deliberately thin.
#
# No wheel install on purpose: backend.py and traces.py resolve the DB and the traces
# directory relative to the source tree (parents[2]), so the package must run from
# /app/src via PYTHONPATH — installing it into site-packages would move those paths
# somewhere else entirely.

FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# Dependencies only, from the lockfile — src is copied after so code changes don't
# bust the dependency layer.
COPY pyproject.toml uv.lock ./
RUN uv export --frozen --no-dev --format requirements-txt -o requirements.txt \
    && uv pip install --system -r requirements.txt

COPY src ./src
COPY corpus ./corpus

ENV PYTHONPATH=/app/src

# Seed the mock org at build time: the org is deterministic (seed.py), the UI reads it
# on every request, and nothing is persistent across restarts anyway.
RUN python -m support_triage.seed --reset

EXPOSE 8000
CMD ["python", "-m", "uvicorn", "support_triage.web:app", "--host", "0.0.0.0", "--port", "8000"]

# 重用已部署系統／OCR runtime，依 lock 同步後端 Python 套件。系統或 OCR 變更仍需完整建置。
ARG RUNTIME_IMAGE=studydy-backend-runtime:local
FROM ${RUNTIME_IMAGE}
USER root
COPY backend/pyproject.toml backend/uv.lock /app/backend/
RUN --mount=type=cache,target=/root/.cache/uv uv sync --project backend --locked --no-dev --python 3.12.14 \
    && chmod -R a+rX /app/backend/.venv
RUN rm -rf /app/backend/src /app/backend/migrations
COPY backend/src /app/backend/src
COPY backend/migrations /app/backend/migrations
COPY local_ai/runtime-lock.json /app/local_ai/runtime-lock.json
RUN chmod -R a+rX /app/backend/src /app/backend/migrations
USER 1000:1000

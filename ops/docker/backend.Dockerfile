FROM ubuntu:26.04@sha256:da6fc2be547864451aa253836dd926da33623312df4a9a243e35dc877c378a78 AS dependencies
COPY --from=ghcr.io/astral-sh/uv:latest@sha256:04d046b13e60d6bcec73cbc5e1cad25d680dea90c8573340950a0ac2d1aef424 /uv /usr/local/bin/uv
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    ca-certificates tzdata bubblewrap fontconfig fonts-noto-cjk \
    libreoffice-writer-nogui=4:26.2.5.2-0ubuntu0.26.04.1 \
    libreoffice-impress-nogui=4:26.2.5.2-0ubuntu0.26.04.1 \
    && rm -rf /var/lib/apt/lists/*
ENV UV_PYTHON_INSTALL_DIR=/opt/python
RUN uv python install 3.12.14 && chmod -R a+rX /opt/python
WORKDIR /app
COPY backend/pyproject.toml backend/uv.lock /app/backend/
RUN --mount=type=cache,target=/root/.cache/uv uv sync --project backend --locked --no-dev --python 3.12.14 \
    && chmod -R a+rX /app/backend/.venv
ENV PYTHONPATH=/app/backend/src \
    PYTHONDONTWRITEBYTECODE=1 \
    STUDYDY_PROFILE=local \
    STUDYDY_LOCAL_RUNTIME_ROOT=/opt/studydy \
    STUDYDY_ARTIFACT_ROOT=/data/artifacts \
    XDG_CACHE_HOME=/tmp/cache \
    HF_HOME=/tmp/huggingface

FROM dependencies AS ocr-packages
RUN --mount=type=cache,target=/root/.cache/uv uv venv --python 3.12.14 /opt/studydy/ocr/runtime \
    && uv pip install --python /opt/studydy/ocr/runtime/bin/python \
       --index-url https://download.pytorch.org/whl/cu128 torch==2.10.0+cu128 torchvision==0.25.0+cu128 \
    && uv pip install --python /opt/studydy/ocr/runtime/bin/python \
       transformers==4.57.1 einops==0.8.2 easydict==1.13 addict==2.4.0 \
    && chmod -R a+rX /opt/studydy

FROM dependencies AS backend
COPY backend/src /app/backend/src
COPY backend/migrations /app/backend/migrations
COPY local_ai/runtime-lock.json /app/local_ai/runtime-lock.json
RUN chmod -R a+rX /app/backend/src /app/backend/migrations /app/local_ai
USER 1000:1000
ENTRYPOINT ["/app/backend/.venv/bin/python", "-m", "runtime.container_app"]
CMD ["serve"]

FROM backend AS ocr
USER root
COPY --link --from=ocr-packages /opt/studydy/ocr/runtime /opt/studydy/ocr/runtime
COPY local_ai /app/local_ai
RUN uv pip install --python /opt/studydy/ocr/runtime/bin/python --no-deps /app/local_ai \
    && chmod -R a+rX /app/local_ai
USER 1000:1000

FROM backend AS test
USER root
RUN --mount=type=cache,target=/root/.cache/uv uv sync --project backend --locked --no-dev --extra test --python 3.12.14
COPY backend/tests /app/backend/tests
COPY local_ai/src /app/local_ai/src
COPY local_ai/tests /app/local_ai/tests
RUN chmod -R a+rX /app
ENV PYTHONPATH=/app/backend/src:/app/backend/tests:/app/local_ai/src
USER 1000:1000
ENTRYPOINT ["/app/backend/.venv/bin/pytest"]
CMD ["-q", "-o", "cache_dir=/tmp/pytest-cache", "backend/tests", "--ignore=backend/tests/runtime", "local_ai/tests"]

FROM dependencies AS ssh-bridge
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends openssh-client \
    && rm -rf /var/lib/apt/lists/*
COPY backend/src/runtime/ssh_bridge.py /app/ssh_bridge.py
RUN chmod 644 /app/ssh_bridge.py
USER 1000:1000
ENTRYPOINT ["/app/backend/.venv/bin/python", "/app/ssh_bridge.py"]

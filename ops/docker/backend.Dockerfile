FROM ubuntu:26.04@sha256:da6fc2be547864451aa253836dd926da33623312df4a9a243e35dc877c378a78 AS runtime-base
COPY --from=ghcr.io/astral-sh/uv:0.13.5@sha256:32a7bb39b496babbd997b0a39a4d66c126bdaf5661b302bf6f06abda8deca6ff /uv /usr/local/bin/uv
# 保留已驗證的轉檔版本；一般套件站不保證保存已被取代的版本。
# Ubuntu snapshot：https://snapshot.ubuntu.com/
ARG UBUNTU_SNAPSHOT=20261005T060000Z
# 新快照已不含指定 LibreOffice，安全套件另用新快照定點更新。
ARG UBUNTU_SECURITY_SNAPSHOT=20261023T060000Z
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates \
    && apt-get update --snapshot "$UBUNTU_SNAPSHOT" \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends --snapshot "$UBUNTU_SNAPSHOT" \
    tzdata bubblewrap fontconfig fonts-noto-cjk \
    libreoffice-writer-nogui=4:26.2.5.2-0ubuntu0.26.04.1 \
    libreoffice-impress-nogui=4:26.2.5.2-0ubuntu0.26.04.1 \
    && apt-get update --snapshot "$UBUNTU_SECURITY_SNAPSHOT" \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends --snapshot "$UBUNTU_SECURITY_SNAPSHOT" \
       ca-certificates=20260816ubuntu0.26.04.1 \
       libxml2-16=2.15.2+dfsg-0.1ubuntu0.3 \
       libxslt1.1=1.1.45-0.1 \
       libcurl4t64=8.18.0-1ubuntu2.8 \
       libcurl3t64-gnutls=8.18.0-1ubuntu2.8 \
    && rm -rf /var/lib/apt/lists/*
ENV UV_PYTHON_INSTALL_DIR=/opt/python
RUN uv python install 3.12.15 && chmod -R a+rX /opt/python
# 正式 lxml 6.1.3 wheel 仍內含舊 libxml2；暫時自建並動態連結 Ubuntu 系統庫。
# 安全修補由上述套件快照提供；上游 wheel 內含 libxml2 >=2.15.4 時移除此階段。
FROM runtime-base AS lxml-build
RUN apt-get update --snapshot "$UBUNTU_SECURITY_SNAPSHOT" \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends --snapshot "$UBUNTU_SECURITY_SNAPSHOT" \
       build-essential pkg-config \
       libxml2-dev=2.15.2+dfsg-0.1ubuntu0.3 \
       libxslt1-dev=1.1.45-0.1 \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /build
COPY backend/uv.lock /build/uv.lock
RUN uv run --no-project --python 3.12.15 python - <<'PYTHON'
import hashlib
import pathlib
import tomllib
import urllib.request

locked = tomllib.loads(pathlib.Path("uv.lock").read_text())
lxml = next(package for package in locked["package"] if package["name"] == "lxml")
source = lxml["sdist"]
with urllib.request.urlopen(source["url"]) as response:
    archive = response.read()
if "sha256:" + hashlib.sha256(archive).hexdigest() != source["hash"]:
    raise ValueError("lxml source checksum mismatch")
pathlib.Path("lxml.tar.gz").write_bytes(archive)
PYTHON
RUN uv venv --python 3.12.15 /build/venv \
    && uv pip install --python /build/venv/bin/python setuptools==83.0.0
RUN uv build --python /build/venv/bin/python --no-build-isolation --wheel --out-dir /wheels /build/lxml.tar.gz

FROM runtime-base AS dependencies
WORKDIR /app
COPY backend/pyproject.toml backend/uv.lock /app/backend/
# app-only／test 同步也須重用此 wheel，避免退回 PyPI 內含的舊 libxml2。
COPY --from=lxml-build /wheels /opt/studydy/lxml
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --project backend --locked --no-dev --no-install-package lxml --python 3.12.15 \
    && uv pip install --python /app/backend/.venv/bin/python --no-deps /opt/studydy/lxml/lxml-*.whl \
    && TIKTOKEN_CACHE_DIR=/opt/studydy/tokenizer-cache /app/backend/.venv/bin/python -c "import tiktoken; tiktoken.get_encoding('o200k_base')" \
    && chmod -R a+rX /opt/studydy/tokenizer-cache \
    && chmod -R a+rX /app/backend/.venv
ENV PYTHONPATH=/app/backend/src \
    PYTHONDONTWRITEBYTECODE=1 \
    STUDYDY_PROFILE=local \
    STUDYDY_LOCAL_RUNTIME_ROOT=/opt/studydy \
    STUDYDY_ARTIFACT_ROOT=/data/artifacts \
    XDG_CACHE_HOME=/tmp/cache \
    HF_HOME=/tmp/huggingface

FROM dependencies AS ocr-packages
RUN --mount=type=cache,target=/root/.cache/uv uv venv --python 3.12.15 /opt/studydy/ocr/runtime \
    && uv pip install --python /opt/studydy/ocr/runtime/bin/python \
       --index-url https://download.pytorch.org/whl/cu128 torch==2.10.0+cu128 torchvision==0.25.0+cu128 \
    && uv pip install --python /opt/studydy/ocr/runtime/bin/python \
       certifi==2026.10.19 transformers==4.57.1 einops==0.8.2 easydict==1.13 addict==2.4.0 matplotlib==3.10.8 \
    && chmod -R a+rX /opt/studydy

FROM dependencies AS backend
ENV TIKTOKEN_CACHE_DIR=/opt/studydy/tokenizer-cache
COPY backend/src /app/backend/src
COPY backend/migrations /app/backend/migrations
COPY local_ai/runtime-lock.json /app/local_ai/runtime-lock.json
RUN chmod -R a+rX /app/backend/src /app/backend/migrations /app/local_ai
USER 20001:1000
ENTRYPOINT ["/app/backend/.venv/bin/python", "-m", "runtime.container_app"]
CMD ["serve"]

FROM backend AS ocr
USER root
COPY --link --from=ocr-packages /opt/studydy/ocr/runtime /opt/studydy/ocr/runtime
COPY local_ai /app/local_ai
RUN uv pip install --python /opt/studydy/ocr/runtime/bin/python --no-deps /app/local_ai \
    && chmod -R a+rX /app/local_ai
USER 20001:1000

FROM backend AS test
USER root
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --project backend --locked --no-dev --extra test --no-install-package lxml --python 3.12.15 \
    && uv pip install --python /app/backend/.venv/bin/python --no-deps /opt/studydy/lxml/lxml-*.whl
COPY backend/tests /app/backend/tests
COPY ops/podcast /app/ops/podcast
COPY ops/research /app/ops/research
COPY local_ai/src /app/local_ai/src
COPY local_ai/tests /app/local_ai/tests
RUN chmod -R a+rX /app
ENV PYTHONPATH=/app/backend/src:/app/backend/tests:/app/local_ai/src
USER 20001:1000
ENTRYPOINT ["/app/backend/.venv/bin/pytest"]
CMD ["-q", "-o", "cache_dir=/tmp/pytest-cache", "backend/tests", "--ignore=backend/tests/runtime", "local_ai/tests", "ops/research/test_egress.py", "ops/research/test_benchmark.py"]

FROM dependencies AS ssh-bridge
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends openssh-client \
    && rm -rf /var/lib/apt/lists/*
COPY backend/src/runtime/ssh_bridge.py /app/ssh_bridge.py
RUN chmod 644 /app/ssh_bridge.py
USER 1000:1000
ENTRYPOINT ["/app/backend/.venv/bin/python", "/app/ssh_bridge.py"]

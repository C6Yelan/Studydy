# 依賴未變更時重用已部署 runtime，避免重複複製大型 OCR 環境。
ARG RUNTIME_IMAGE=studydy-backend-runtime:local
FROM ${RUNTIME_IMAGE}
USER root
RUN rm -rf /app/backend/src /app/backend/migrations
COPY backend/src /app/backend/src
COPY backend/migrations /app/backend/migrations
RUN chmod -R a+rX /app/backend/src /app/backend/migrations
USER 1000:1000

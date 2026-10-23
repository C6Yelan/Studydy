FROM node:24-bookworm-slim@sha256:55fb5f5a527ea2c5c4a95f963e48ef90aa38c7b346054fdc8ee9957a2b55d205 AS build
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm test && npm run build

FROM nginxinc/nginx-unprivileged:1.30.6-alpine@sha256:f5116ed4606f3eb015dd4a8e28cb9b351476dc0ad4bc6e9f93b19cec130cdf03
COPY --from=build /app/dist /usr/share/nginx/html
COPY ops/docker/nginx.conf /etc/nginx/studydy.conf.template
COPY ops/docker/start-frontend.sh /usr/local/bin/start-frontend.sh
ENTRYPOINT ["/bin/sh", "/usr/local/bin/start-frontend.sh"]
USER 101:101
EXPOSE 8080

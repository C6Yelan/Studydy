FROM node:24-bookworm-slim@sha256:dc26ae92c9fb15cf6b1d63e20f0944b2efe0f115b20e00be48fb23fceb061c54 AS build
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
# note-latex.test.mjs 以相對路徑讀後端白名單，核對前後端支援的 LaTeX 指令一致。
COPY backend/src/runtime/note_latex.py /backend/src/runtime/note_latex.py
RUN npm test && npm run build

FROM nginxinc/nginx-unprivileged:1.30.6-alpine@sha256:f5116ed4606f3eb015dd4a8e28cb9b351476dc0ad4bc6e9f93b19cec130cdf03
COPY --from=build /app/dist /usr/share/nginx/html
COPY ops/docker/nginx.conf /etc/nginx/studydy.conf.template
COPY ops/docker/start-frontend.sh /usr/local/bin/start-frontend.sh
ENTRYPOINT ["/bin/sh", "/usr/local/bin/start-frontend.sh"]
USER 101:101
EXPOSE 8080

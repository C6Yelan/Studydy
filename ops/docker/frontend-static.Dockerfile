# context 只包含已驗證的 site/ 與 nginx.conf，沿用既有 Nginx runtime。
ARG RUNTIME_IMAGE=studydy-frontend-runtime:local
FROM ${RUNTIME_IMAGE}
USER root
RUN rm -rf /usr/share/nginx/html
COPY site /usr/share/nginx/html
COPY nginx.conf /etc/nginx/studydy.conf.template
USER 101:101

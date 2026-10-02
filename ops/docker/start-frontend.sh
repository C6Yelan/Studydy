#!/bin/sh
set -eu
# 與 backend 共用 bytes 設定；拒絕無效值，避免 Nginx 的 0 代表無上限。
: "${STUDYDY_UPLOAD_MAX_BYTES:=104857600}"
case "$STUDYDY_UPLOAD_MAX_BYTES" in ''|*[!0-9]*) exit 1 ;; esac
[ "$STUDYDY_UPLOAD_MAX_BYTES" -ge 1 ] && [ "$STUDYDY_UPLOAD_MAX_BYTES" -le 104857600 ]
export STUDYDY_UPLOAD_MAX_BYTES
envsubst '${STUDYDY_UPLOAD_MAX_BYTES}' < /etc/nginx/studydy.conf.template > /tmp/studydy.conf
sed 's@include /etc/nginx/conf.d/\*.conf;@include /tmp/studydy.conf;@' /etc/nginx/nginx.conf > /tmp/nginx.conf
exec nginx -c /tmp/nginx.conf -g 'daemon off;'

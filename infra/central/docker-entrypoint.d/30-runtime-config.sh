#!/bin/sh
# FCC Central Hub — generate the SPA runtime-config from env (B1/P13).
#
# The official nginx image runs every /docker-entrypoint.d/*.sh before nginx
# starts. We substitute the browser-facing endpoints from the SAME env vars that
# docker-compose.central.yml publishes the ports with, so the SPA endpoints
# DERIVE from the single port SSOT (central.env.example) — no hardcoded port can
# drift. envsubst is restricted to the known placeholders so nothing else in the
# template (booleans, literals) is touched.
#
# SAME-ORIGIN GATEWAY: the SPA's API/WS endpoints point at the web gateway
# (${PUBLIC_HOST}:${WEB_PORT}); nginx path-routes /headless,/platform to the API
# containers. So the API host ports are NOT template vars — only the gateway +
# Keycloak origin vars are substituted.
set -eu

# Defaults mirror central.env.example so a bare `docker run` still produces a
# coherent config. compose passes the real values via the web service env.
: "${PUBLIC_HOST:=127.0.0.1}"
: "${WEB_PORT:=8080}"
# Secure by default: only an explicit opt-in relaxes the SPA https rule.
: "${ALLOW_INSECURE_TRANSPORT:=false}"
: "${KEYCLOAK_PORT:=8081}"
: "${OIDC_REALM:=fcc-dev}"
: "${OIDC_CLIENT_ID:=fcc-platform-frontend}"
# Login strategy — oidc (Keycloak redirect) or local (email+password form).
: "${WEB_AUTH_MODE:=oidc}"
export PUBLIC_HOST WEB_PORT KEYCLOAK_PORT OIDC_REALM OIDC_CLIENT_ID ALLOW_INSECURE_TRANSPORT WEB_AUTH_MODE

template=/etc/nginx/runtime-config.central.js.template
output=/usr/share/nginx/html/runtime-config.js

envsubst '${PUBLIC_HOST} ${WEB_PORT} ${KEYCLOAK_PORT} ${OIDC_REALM} ${OIDC_CLIENT_ID} ${ALLOW_INSECURE_TRANSPORT} ${WEB_AUTH_MODE}' \
    < "$template" > "$output"

echo "[runtime-config] generated $output from env (PUBLIC_HOST=$PUBLIC_HOST"\
" WEB_PORT=$WEB_PORT KEYCLOAK_PORT=$KEYCLOAK_PORT AUTH_MODE=$WEB_AUTH_MODE) — same-origin gateway"

#!/bin/sh
# FCC Central Hub — widen the SPA's CSP connect-src to the IdP + WS origins.
#
# The built index.html ships the PRODUCTION meta CSP
# (connect-src 'self' https: wss:), which assumes an https IdP reachable over
# TLS. The central stack, however, serves Keycloak over HTTP on a DIFFERENT
# origin (${PUBLIC_HOST}:${KEYCLOAK_PORT}) and the chamber-progress WebSocket
# over ws:// on the gateway origin (${PUBLIC_HOST}:${WEB_PORT}). Neither matches
# 'self' (same-origin only), https:, or wss:, so without this the browser blocks
# the OIDC discovery/token fetch — the SPA shows "cannot connect to auth server".
#
# We DERIVE the allowed origins from the SAME env vars 30-runtime-config.sh uses
# to generate runtime-config.js (PUBLIC_HOST / KEYCLOAK_PORT / WEB_PORT), so the
# CSP and the runtime endpoints can never drift and no host/port literal lives
# here. In an https deployment the IdP/WS endpoints are https/wss and already
# covered by the prod directive; the extra http/ws origins are simply unused.
#
# Idempotent: only the canonical prod connect-src is rewritten, so a plain
# `docker restart` (writable layer already patched) is a no-op.
set -eu

: "${PUBLIC_HOST:=127.0.0.1}"
: "${WEB_PORT:=8080}"
: "${KEYCLOAK_PORT:=8081}"

index=/usr/share/nginx/html/index.html
kc_origin="http://${PUBLIC_HOST}:${KEYCLOAK_PORT}"
ws_origin="ws://${PUBLIC_HOST}:${WEB_PORT}"

if grep -q "connect-src 'self' https: wss:" "$index"; then
    sed -i \
        "s|connect-src 'self' https: wss:|connect-src 'self' ${kc_origin} ${ws_origin} https: wss:|" \
        "$index"
    echo "[csp] connect-src += ${kc_origin} ${ws_origin} (IdP + WS origins, env-derived)"
else
    echo "[csp] canonical prod connect-src not found — already patched or template changed"
fi

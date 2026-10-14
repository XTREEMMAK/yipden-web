# syntax=docker/dockerfile:1

# Static output is portable; build on the builder's native CPU without QEMU.
FROM --platform=$BUILDPLATFORM node:22-alpine AS build
WORKDIR /app
# Baked in at build time; the site has no server process to read them later (see README).
ARG SITE_ORIGIN=https://yipden.com
ARG REMOVAL_WEBHOOK_URL=
ARG TURNSTILE_SITE_KEY=
ARG CONTACT_EMAIL=jamaal@j2it.us
ENV SITE_ORIGIN=$SITE_ORIGIN REMOVAL_WEBHOOK_URL=$REMOVAL_WEBHOOK_URL TURNSTILE_SITE_KEY=$TURNSTILE_SITE_KEY CONTACT_EMAIL=$CONTACT_EMAIL
COPY package.json package-lock.json ./
RUN npm ci
COPY .eleventy.js ./
COPY src ./src
RUN npm run build

FROM caddy:2-alpine AS runtime
ARG PUID=1000
ARG PGID=1000
RUN mkdir -p /config/caddy /data/caddy && chown -R ${PUID}:${PGID} /config /data
COPY Caddyfile /etc/caddy/Caddyfile
COPY --from=build --chown=${PUID}:${PGID} /app/_site /srv
USER ${PUID}:${PGID}
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD wget -qO /dev/null http://127.0.0.1:8080/ || exit 1
CMD ["caddy", "run", "--config", "/etc/caddy/Caddyfile", "--adapter", "caddyfile"]

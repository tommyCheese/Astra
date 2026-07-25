FROM node:22.17.1-bookworm-slim AS build

WORKDIR /opt/astra/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend ./
RUN npm run build

FROM nginx:1.27.5-alpine
COPY deploy/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /opt/astra/frontend/dist /usr/share/nginx/html

EXPOSE 80

HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=6 \
  CMD wget -q -O /dev/null http://127.0.0.1/healthz || exit 1

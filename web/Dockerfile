FROM node:24.21.0-alpine AS deps
# pnpm's official standalone build for musl on amd64; the checksum pins the exact artifact.
ADD --checksum=sha256:cc66c1558a1a03b320ff5b4022f755386fa1de54db3f97ff624112015be1ccf1 \
  https://github.com/pnpm/pnpm/releases/download/v11.27.1/pnpm-linux-x64-musl.tar.gz /tmp/pnpm.tar.gz
RUN mkdir /opt/pnpm && tar -xzf /tmp/pnpm.tar.gz -C /opt/pnpm && rm /tmp/pnpm.tar.gz
# CI=true keeps pnpm from printing its update notice; the version is pinned on purpose.
ENV PATH=/opt/pnpm:$PATH PNPM_HOME=/pnpm CI=true
WORKDIR /app
COPY package.json pnpm-lock.yaml ./
RUN --mount=type=cache,id=pnpm,target=/pnpm/store pnpm install --frozen-lockfile

FROM deps AS source
COPY . .

FROM source AS dev-server
CMD ["pnpm", "run", "dev", "--host", "--port", "8080"]

FROM source AS build
# The SPA fallback only serves routes that are not prerendered; nginx answers those with 404 instead.
RUN pnpm run build && rm dist/client/__spa-fallback.html

FROM nginxinc/nginx-unprivileged:1.30.5-alpine-slim AS serve
# The entrypoint renders templates with envsubst; the filter keeps nginx's own $variables untouched.
ENV NGINX_ENTRYPOINT_WORKER_PROCESSES_AUTOTUNE=1 NGINX_ENVSUBST_FILTER=^API_
RUN mkdir /etc/nginx/templates
COPY --chmod=644 nginx/default.conf.template /etc/nginx/templates/
COPY --from=build /app/dist/client /srv/www

FROM node:24.22.0-alpine AS deps
# pnpm's official standalone build for musl on amd64; the checksum pins the exact artifact.
ADD --checksum=sha256:d7115e2f2791d79bf9e7e448609537595bb7787de1ef5292ce91ff3d1be2629e \
  https://github.com/pnpm/pnpm/releases/download/v12.14.0/pnpm-linux-x64-musl.tar.gz /tmp/pnpm.tar.gz
RUN mkdir /opt/pnpm && tar -xzf /tmp/pnpm.tar.gz -C /opt/pnpm && rm /tmp/pnpm.tar.gz
# CI=true keeps pnpm from printing its update notice; the version is pinned on purpose.
ENV PATH=/opt/pnpm:$PATH PNPM_HOME=/pnpm CI=true
WORKDIR /app
# pnpm-workspace.yaml carries the release-age exclusions the lockfile policy check needs.
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
RUN --mount=type=cache,id=pnpm,target=/pnpm/store pnpm install --frozen-lockfile

FROM deps AS source
COPY . .

FROM source AS dev-server
CMD ["pnpm", "run", "dev", "--host", "--port", "8080"]

FROM source AS build
RUN pnpm run build

FROM nginxinc/nginx-unprivileged:1.30.6-alpine-slim AS serve
# The entrypoint renders templates with envsubst; the filter keeps nginx's own $variables untouched.
ENV NGINX_ENTRYPOINT_WORKER_PROCESSES_AUTOTUNE=1 NGINX_ENVSUBST_FILTER=^API_
RUN mkdir /etc/nginx/templates
COPY --chmod=644 nginx/default.conf.template /etc/nginx/templates/
COPY --from=build /app/dist/client /srv/www

FROM caddy:2.11.9@sha256:0c2243efcf437ab7f7a978f1027fcf0d4f9be8a75df27b1b3fbc5dbe0ffa8cf0
ARG TARGETARCH
ENV CADDY_VERSION=v2.11.8
RUN set -eu; \
    case "$TARGETARCH" in \
      amd64) checksum=c863699eee84c6b410573de2ec67af81d07eb054f9020bb98f3c6cb256fea4be2fd1acfbd39110c649c3338282593bba3d3ed6424d91b7f5675e8b877a8999e9 ;; \
      arm64) checksum=6a285426268c8f987e0c459f61dc5af3c25e663955c0e4dddc6e187722b7cdd15e4233ff0e570a4ee17b3c68d59e2a61ce96d2f6be55ea58eead6a334859557c ;; \
      *) exit 1 ;; \
    esac; \
    curl --fail --silent --show-error --location --output /tmp/caddy.tar.gz "https://github.com/caddyserver/caddy/releases/download/${CADDY_VERSION}/caddy_2.11.8_linux_${TARGETARCH}.tar.gz"; \
    printf '%s  /tmp/caddy.tar.gz\n' "$checksum" | sha512sum -c; \
    tar -xzf /tmp/caddy.tar.gz -C /usr/bin caddy; \
    rm /tmp/caddy.tar.gz; \
    setcap cap_net_bind_service=+p /usr/bin/caddy; \
    caddy version
LABEL org.opencontainers.image.version=v2.11.8 \
      org.opencontainers.image.source="https://github.com/bartkozik/portfolio-infra"

# Portfolio infrastructure

Container deployment for [kozikowski.it](https://kozikowski.it), with room for separately isolated demo services on the same VPS. The application is built in its own CI pipeline and published as `ghcr.io/bartkozik/kozikowski-it`. This repository contains server configuration only.

## Runtime

- Ubuntu 24.04 LTS, Docker Engine and Compose from Docker's official repository.
- Caddy provides HTTPS for `kozikowski.it` and redirects `www.kozikowski.it` to the canonical domain. Its image is pinned by digest.
- Two application slots share a dedicated Docker network. Only Caddy publishes ports: TCP 80/443 and UDP 443.
- Each application has a 768 MiB memory limit, one CPU, a read-only filesystem and UID/GID 1000. Caddy has 256 MiB and half a CPU. All containers have process limits and rotated logs.
- Application images must include a healthcheck and serve `GET /healthz` with HTTP 200 on port 3000. Writable temporary storage is limited to `/tmp` and `/app/.next/cache`.

An 8 GB VPS leaves headroom for two application versions and future demos. No cloud resources are provisioned by this repository.

## First server setup

Create a fresh Ubuntu 24.04 VPS and verify administrator SSH access. Point DNS A records for `kozikowski.it` and `www.kozikowski.it` at the server; add AAAA records only when IPv6 works. Allow inbound TCP 80/443, UDP 443 and the existing administrator SSH port in the provider firewall. Bootstrap does not modify firewall rules or administrator SSH settings. Docker-published ports bypass some host firewall rules; use the provider firewall for the outer boundary.

Create a dedicated Ed25519 deployment key outside this repository. Keep its private key in the application's protected CI environment. Transfer this repository and the public key using the administrator connection, then run on the server:

```sh
sudo ./bin/bootstrap /path/to/portfolio-deploy.pub
```

Bootstrap installs root-owned runtime files in `/opt/portfolio`, creates the restricted `portfolio-deploy` account and starts Caddy in maintenance mode. It can be rerun; an existing application route and deployment state are preserved. Conflicting distribution Docker packages require an administrator to review their removal first.

Keep the administrator session open while testing another administrator connection and the deployment key. The deploy account cannot open a shell, allocate a terminal, forward connections, edit runtime files or access Docker directly. Its only accepted SSH command is:

```sh
ssh -i /path/to/deploy-key -o StrictHostKeyChecking=yes portfolio-deploy@SERVER \
  'deploy sha256:REPLACE_WITH_64_LOWERCASE_HEXADECIMAL_CHARACTERS'
```

Configure the application's protected GitHub environment with its SSH private key, server address and pinned `known_hosts` entry. The deployment workflow connects on SSH port 22. Verify the server's host key fingerprint through the provider console or an already trusted administrator channel before saving it. The workflow sends the digest returned by the image build, never a mutable image tag.

Make the GHCR image public before the first deployment. If it remains private, an administrator must authenticate root to GHCR with a read-only package credential; do not grant package publishing or repository access to the server.

## Deployment and rollback

The forced SSH command validates the exact command format. A limited sudo rule invokes a root-owned helper, which validates the digest again and always uses the fixed application repository. No source files or builds run on the server.

The helper takes a non-blocking deployment lock, pulls the immutable image, starts the inactive slot and waits up to 120 seconds for container health. It atomically replaces the Caddy configuration, validates and reloads it, then checks an internal HTTP route through the same upstream for HTTP 200 and the expected digest header. The internal verification listener is not published to the host. Public DNS, certificates and external routing must also be checked after the first live deployment:

```sh
curl --fail --show-error https://kozikowski.it/healthz
curl --head https://www.kozikowski.it/
```

On failure, the previous proxy configuration and deployment state are restored and the prior route is checked again. The previous active container is retained. A root-only transaction journal lets the next deployment recover an interrupted operation before starting new work. A failed rollback leaves the journal in place and requires administrator attention; inspect Caddy and application logs before retrying.

To roll back a completed deployment, deploy the previous known-good digest through the same SSH command. The last active container stays running until its slot is reused by a later deployment. Images are never pruned automatically; retain the current and previous digests when scheduling administrative cleanup. Back up Caddy's `portfolio_caddy-data` volume to retain certificate state.

Administrator inspection:

```sh
sudo cat /opt/portfolio/state/deployment.json
sudo docker ps --filter label=com.docker.compose.project=portfolio
sudo docker logs --tail 100 portfolio-caddy-1
```

## Local verification

```sh
python3 -m unittest discover -s tests -v
bash -n bin/bootstrap bin/portfolio-ssh
APP_BLUE_IMAGE=ghcr.io/bartkozik/kozikowski-it:check \
APP_GREEN_IMAGE=ghcr.io/bartkozik/kozikowski-it:check \
docker compose config --quiet
```

Tests exercise digest validation, concurrent deployment rejection, successful slot rotation, failure rollback and interrupted transaction recovery. They do not provision a server or request certificates. Bootstrap requires a fresh Ubuntu host and is not run by the local test suite.

With a locally built application image available, run the Docker integration test:

```sh
python3 tests/integration.py kozikowski-it:review
```

It creates an isolated Compose project, disables certificate issuance, publishes only an ephemeral loopback port and removes its own containers and volumes afterwards. It checks real Caddy reloads, slot retention, a stopped candidate and rollback after a routed health failure. Local image references replace registry pulls only inside this test harness.

Future demos should use their own image repositories, restricted deployment users, Compose projects, networks, resource budgets and Caddy routes. They should not receive the portfolio deployment key or access to its state.

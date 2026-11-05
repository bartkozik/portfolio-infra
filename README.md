# Portfolio infrastructure

Docker Compose and Caddy configuration for [kozikowski-it](https://github.com/bartkozik/kozikowski-it), with image-based deployment, health checks and rollback on a single Ubuntu VPS.

The application is built and tested in GitHub Actions. The server pulls the resulting image from GHCR; it does not build application code.

## Deployment

1. CI sends an immutable image digest over SSH.
2. The deployment script starts the image in the inactive application slot and waits for its health check.
3. Caddy switches traffic to that slot, then the script checks the application through the proxy.
4. If the deployment fails, the script restores the previous route. The previous container remains available for rollback.

A deployment lock prevents overlapping releases. A transaction journal allows the next deployment to recover an interrupted switch. A completed release can be rolled back by deploying a previous image digest.

## Runtime

- Ubuntu 24.04 LTS, Docker Engine, Docker Compose and Caddy.
- HTTPS for `kozikowski.it`, with `www` redirected to the canonical domain.
- Non-root application containers with read-only filesystems, resource limits and rotated logs.
- Only Caddy exposes public application ports; application containers share a private Docker network.
- The SSH deployment account accepts only a validated digest for the application’s image repository. It has no interactive shell or direct Docker access.

## Caddy image

The Caddy image packages the official release binary with a verified SHA-512 checksum. GitHub Actions checks the Go security baseline, configuration validation, proxying and reload before publishing it to GHCR. Production uses an immutable image digest in `compose.yaml`.

## Server setup

Point the domain’s DNS records at the VPS and allow SSH on port 22, HTTP on port 80 and HTTPS on port 443. Using an administrator connection, transfer this repository and a dedicated Ed25519 public deployment key, then run:

```sh
sudo ./bin/bootstrap /path/to/portfolio-deploy.pub
```

The script installs Docker, configures the restricted deployment account and creates the runtime under `/opt/portfolio`. Keep the administrator connection open until a second SSH connection has been verified.

The application workflow uses `DEPLOY_HOST`, `DEPLOY_SSH_KEY` and `DEPLOY_KNOWN_HOSTS` from its GitHub `production` environment. Verify the host fingerprint through the provider console. The server needs read access to the GHCR image. Enable `PRODUCTION_ENABLED` only after checking SSH access, DNS and HTTPS.

## Traffic demo

`traffic/compose.yaml` runs the PM Traffic demo with a private PostgreSQL volume and two API/web deployment slots. Caddy imports its routes independently, so portfolio releases preserve the demo route.

After the portfolio bootstrap, run `sudo bash bin/bootstrap-traffic /path/to/traffic-deploy.pub`. It creates a separate restricted deployment account and generates database credentials in root-only files under `/opt/traffic`. Set the dedicated `PTM_DEMO_OPENAI_API_KEY` in `/opt/traffic/api.env` using an administrator session; never place it in Git or an image build argument.

The application workflow supplies two tested image digests to `traffic-deploy`. API and web switch together after both health checks pass. The previous pair remains available for rollback. Both applications use the same deployment lock while changing Caddy routes. The demo's AI budget is persisted in PostgreSQL, independently of the running image and guest resets.

## Verification

```sh
python3 -m unittest discover -s tests -v
bash -n bin/bootstrap bin/portfolio-ssh bin/bootstrap-traffic bin/traffic-ssh
bash tests/validate_traffic_config.sh
```

Tests cover input validation, concurrent deployments, slot switching, rollback and recovery after interruption. CI also scans Git history for secrets and validates the Compose configuration.

For a local Docker integration test, first build the application image using the [application README](https://github.com/bartkozik/kozikowski-it#docker-and-deployment), then run:

```sh
python3 tests/integration.py kozikowski-it:local
```

This tests real containers and Caddy reloads, including failed deployments, in an isolated local environment. It does not test public DNS or certificate issuance.

## EPOCA demo

EPOCA runs a Scala API, a guest-session gateway and a static frontend at `epoca.kozikowski.it`. Each visitor receives a separate temporary organization with fictional data. PostgreSQL and the Scala API are reachable only on the private demo network.

`bin/bootstrap-epoca` creates the database credentials and signing key on the server, installs the restricted deployment account and starts PostgreSQL. The initial release requires API, gateway and frontend image digests. Subsequent releases can replace the backend pair or the frontend while retaining the other components. Each release is checked before Caddy switches traffic; a failed health check restores the previous route.

The gateway stores session limits and reset state in PostgreSQL. Sessions expire after 24 hours; reset rotates the workspace and credentials without extending that lifetime. The public API excludes account registration, company login, invitations, uploads and external integrations.

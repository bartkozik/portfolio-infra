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

## Server setup

Point the domain’s DNS records at the VPS and allow SSH on port 22, HTTP on port 80 and HTTPS on port 443. Using an administrator connection, transfer this repository and a dedicated Ed25519 public deployment key, then run:

```sh
sudo ./bin/bootstrap /path/to/portfolio-deploy.pub
```

The script installs Docker, configures the restricted deployment account and creates the runtime under `/opt/portfolio`. Keep the administrator connection open until a second SSH connection has been verified.

The application workflow uses `DEPLOY_HOST`, `DEPLOY_SSH_KEY` and `DEPLOY_KNOWN_HOSTS` from its GitHub `production` environment. Verify the host fingerprint through the provider console. The server needs read access to the GHCR image. Enable `PRODUCTION_ENABLED` only after checking SSH access, DNS and HTTPS.

## Verification

```sh
python3 -m unittest discover -s tests -v
bash -n bin/bootstrap bin/portfolio-ssh
```

Tests cover input validation, concurrent deployments, slot switching, rollback and recovery after interruption. CI also scans Git history for secrets and validates the Compose configuration.

For a local Docker integration test, first build the application image using the [application README](https://github.com/bartkozik/kozikowski-it#docker-and-deployment), then run:

```sh
python3 tests/integration.py kozikowski-it:local
```

This tests real containers and Caddy reloads, including failed deployments, in an isolated local environment. It does not test public DNS or certificate issuance.

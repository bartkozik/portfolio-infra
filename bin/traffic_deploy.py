#!/usr/bin/python3 -I
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import signal
import sys
import time

spec = importlib.util.spec_from_file_location("portfolio_deploy", Path(__file__).with_name("deploy.py"))
deploy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deploy)
ROOT = Path("/opt/traffic")
PORTFOLIO = Path("/opt/portfolio")
IMAGES = {"api": "ghcr.io/bartkozik/pm-traffic-manager-api", "web": "ghcr.io/bartkozik/pm-traffic-manager-web"}


def release_identifier(images):
    for component in ("api", "web"):
        deploy.validate_digest(images[component])
    return "sha256:" + hashlib.sha256(json.dumps(images, sort_keys=True).encode()).hexdigest()


def configuration(slot, digest):
    return f'''traffic.kozikowski.it {{
    encode zstd gzip
    header {{
        X-Content-Type-Options nosniff
        Referrer-Policy same-origin
        X-Frame-Options DENY
        X-Traffic-Release "{digest}"
        Permissions-Policy "camera=(), microphone=(), geolocation=()"
        Content-Security-Policy "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; connect-src 'self' wss://traffic.kozikowski.it; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
    }}
    handle /api/* {{
        request_body {{
            max_size 512KB
        }}
        reverse_proxy traffic-api-{slot}:4000
    }}
    handle {{
        reverse_proxy traffic-web-{slot}:8080
    }}
}}

:8081 {{
    header X-Traffic-Release "{digest}"
    handle /api/* {{
        reverse_proxy traffic-api-{slot}:4000
    }}
    handle {{
        reverse_proxy traffic-web-{slot}:8080
    }}
}}
'''


class TrafficRuntime(deploy.DockerRuntime):
    def release(self, digest):
        deploy.validate_digest(digest)
        images = json.loads((self.root / "state" / "releases" / (digest[7:] + ".json")).read_text())
        if release_identifier(images) != digest:
            raise ValueError("Release manifest does not match its digest")
        return images

    def compose(self, arguments, images=None):
        env = dict(deploy.ENV)
        for slot in ("blue", "green"):
            pair = self.release(images[slot]) if images and slot in images else {k: deploy.EMPTY_DIGEST for k in IMAGES}
            for component in IMAGES:
                env[f"{component.upper()}_{slot.upper()}_IMAGE"] = f"{IMAGES[component]}@{pair[component]}"
        return self.run(["compose", "--project-name", "traffic", "--project-directory", str(self.root),
                         "-f", str(self.root / "compose.yaml"), *arguments], env)

    def pull(self, digest):
        for component, image_digest in self.release(digest).items():
            image = f"{IMAGES[component]}@{image_digest}"
            self.run(["pull", image], timeout=600)
            config = json.loads(self.run(["image", "inspect", image]))[0]["Config"]
            if config.get("User") not in ("ptm", "1001:1001", "1000:1000"):
                raise RuntimeError("Demo images must use an unprivileged user")
            if not config.get("Healthcheck", {}).get("Test") or config["Healthcheck"]["Test"][0] == "NONE":
                raise RuntimeError("Demo images must define a healthcheck")

    def start(self, slot, digest, state):
        images = {**state["images"], slot: digest}
        self.compose(["up", "-d", "--no-deps", "--no-build", "--pull", "never", "--force-recreate",
                      f"traffic-api-{slot}", f"traffic-web-{slot}"], images)

    def wait_healthy(self, slot):
        for component in IMAGES:
            identifier = self.container(f"traffic-{component}-{slot}")
            deadline = time.monotonic() + 120
            while time.monotonic() < deadline:
                state = json.loads(self.run(["inspect", identifier]))[0]["State"]
                if state.get("Running") and state.get("Health", {}).get("Status") == "healthy":
                    break
                if not state.get("Running") or state.get("Health", {}).get("Status") == "unhealthy":
                    raise RuntimeError("Candidate demo container failed health check")
                time.sleep(2)
            else:
                raise RuntimeError("Candidate demo did not become healthy")

    def caddy(self):
        return deploy.DockerRuntime(PORTFOLIO)

    def validate(self):
        self.caddy().validate()

    def reload(self):
        self.caddy().reload()

    def probe(self, digest):
        identifier = self.caddy().container("caddy")
        for path in ("/", "/api/health"):
            headers = self.run(["exec", identifier, "sh", "-c",
                                f"wget -S -O /dev/null -T 10 http://127.0.0.1:8081{path} 2>&1"])
            if "200 OK" not in headers or f"x-traffic-release: {digest}" not in headers.lower():
                raise RuntimeError("Routed demo health check failed")


class TrafficDeployer(deploy.Deployer):
    def __init__(self, root, runtime):
        super().__init__(root, runtime)
        self.config = PORTFOLIO / "caddy" / "sites" / "traffic.caddy"

    def configuration(self, slot, digest):
        return configuration(slot, digest)


def main():
    if len(sys.argv) != 3 or os.geteuid() != 0:
        raise ValueError("Usage: sudo traffic-deploy <api sha256:digest> <web sha256:digest>")
    os.umask(0o077)
    for signum in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
        signal.signal(signum, deploy.interrupted)
    images = {"api": deploy.validate_digest(sys.argv[1]), "web": deploy.validate_digest(sys.argv[2])}
    digest = release_identifier(images)
    with (PORTFOLIO / "state" / "deploy.lock").open("a") as lock:
        deploy.fcntl.flock(lock, deploy.fcntl.LOCK_EX | deploy.fcntl.LOCK_NB)
        deploy.atomic_write(ROOT / "state" / "releases" / (digest[7:] + ".json"), json.dumps(images))
        TrafficDeployer(ROOT, TrafficRuntime(ROOT)).deploy(digest)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)

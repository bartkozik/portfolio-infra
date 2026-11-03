#!/usr/bin/python3 -I
import fcntl
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path


ROOT = Path("/opt/portfolio")
IMAGE = "ghcr.io/bartkozik/kozikowski-it"
EMPTY_DIGEST = "sha256:" + "0" * 64
ENV = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "HOME": "/root", "DOCKER_CONFIG": "/root/.docker"}


def validate_digest(value):
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", value):
        raise ValueError("Expected sha256 followed by 64 lowercase hexadecimal characters")
    return value


def atomic_write(path, text, mode=0o600):
    descriptor, temporary = tempfile.mkstemp(prefix=".deploy-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w") as handle:
            os.fchmod(handle.fileno(), mode)
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def configuration(slot, digest):
    return f'''(application) {{
    header X-Deployment-Digest "{digest}"
    reverse_proxy app-{slot}:3000
}}

kozikowski.it {{
    encode zstd gzip
    import application
}}

www.kozikowski.it {{
    redir https://kozikowski.it{{uri}} permanent
}}

:8080 {{
    import application
}}
'''


class DockerRuntime:
    def __init__(self, root):
        self.root = root

    def run(self, arguments, env=None, timeout=180):
        result = subprocess.run(["/usr/bin/docker", *arguments], env=env or ENV,
                                text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                timeout=timeout)
        if result.returncode:
            raise RuntimeError(f"Docker command failed: {result.stderr.strip()}")
        return result.stdout.strip()

    def compose(self, arguments, images=None):
        env = dict(ENV)
        for slot in ("blue", "green"):
            env[f"APP_{slot.upper()}_IMAGE"] = f"{IMAGE}@{(images or {}).get(slot, EMPTY_DIGEST)}"
        return self.run(["compose", "--project-name", "portfolio", "--project-directory", str(self.root),
                         "-f", str(self.root / "compose.yaml"), *arguments], env)

    def pull(self, digest):
        self.run(["pull", f"{IMAGE}@{digest}"], timeout=600)
        data = json.loads(self.run(["image", "inspect", f"{IMAGE}@{digest}"]))[0]
        if data["Config"].get("User") not in ("node", "node:node", "1000", "1000:1000"):
            raise RuntimeError("Image must use the node user or UID 1000")
        if not data["Config"].get("Healthcheck", {}).get("Test") or data["Config"]["Healthcheck"]["Test"][0] == "NONE":
            raise RuntimeError("Image must define a healthcheck")

    def start(self, slot, digest, state):
        images = dict(state["images"])
        images[slot] = digest
        self.compose(["up", "-d", "--no-deps", "--no-build", "--pull", "never", "--force-recreate", f"app-{slot}"], images)

    def container(self, service):
        identifier = self.compose(["ps", "-q", service])
        if not identifier:
            raise RuntimeError(f"Container {service} is not running")
        return identifier

    def wait_healthy(self, slot):
        identifier = self.container(f"app-{slot}")
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            data = json.loads(self.run(["inspect", identifier]))[0]["State"]
            status = data.get("Health", {}).get("Status")
            if data.get("Running") and status == "healthy":
                return
            if not data.get("Running") or status == "unhealthy":
                raise RuntimeError(f"Candidate {slot} failed its healthcheck")
            time.sleep(2)
        raise RuntimeError(f"Candidate {slot} did not become healthy in 120 seconds")

    def validate(self):
        self.run(["exec", self.container("caddy"), "caddy", "validate", "--config", "/etc/caddy/Caddyfile", "--adapter", "caddyfile"])

    def reload(self):
        self.run(["exec", self.container("caddy"), "caddy", "reload", "--config", "/etc/caddy/Caddyfile", "--adapter", "caddyfile"])

    def probe(self, digest):
        identifier = self.container("caddy")
        for attempt in range(10):
            try:
                headers = self.run(["exec", identifier, "sh", "-c", "wget -S -O /dev/null -T 5 http://127.0.0.1:8080/healthz 2>&1"])
                if re.search(r"HTTP/\d(?:\.\d)? 200\b", headers) and f"x-deployment-digest: {digest}" in headers.lower():
                    return
            except RuntimeError:
                pass
            time.sleep(1)
        raise RuntimeError("Routed healthcheck did not return 200 with the requested digest")


class Deployer:
    def __init__(self, root, runtime):
        self.root = root
        self.runtime = runtime
        self.state_file = root / "state" / "deployment.json"
        self.journal = root / "state" / "pending.json"
        self.config = root / "caddy" / "Caddyfile"

    def state(self):
        if not self.state_file.exists():
            return {"active": None, "images": {}}
        state = json.loads(self.state_file.read_text())
        if state["active"] not in (None, "blue", "green"):
            raise RuntimeError("Invalid active deployment slot")
        for slot, digest in state["images"].items():
            if slot not in ("blue", "green"):
                raise RuntimeError("Invalid deployment slot")
            validate_digest(digest)
        if state["active"] and state["active"] not in state["images"]:
            raise RuntimeError("Active deployment digest is missing")
        return state

    def rollback(self):
        previous = json.loads(self.journal.read_text())
        atomic_write(self.config, previous["config"], 0o644)
        self.runtime.reload()
        if previous["state"]["active"]:
            self.runtime.probe(previous["state"]["images"][previous["state"]["active"]])
        atomic_write(self.state_file, json.dumps(previous["state"]) + "\n")
        self.journal.unlink()

    def deploy(self, digest):
        validate_digest(digest)
        with (self.root / "state" / "deploy.lock").open("a") as lock:
            os.fchmod(lock.fileno(), 0o600)
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise RuntimeError("A deployment is already running") from error
            if self.journal.exists():
                self.rollback()
            state = self.state()
            slot = "green" if state["active"] == "blue" else "blue"
            self.runtime.pull(digest)
            previous = {"state": state, "config": self.config.read_text()}
            atomic_write(self.journal, json.dumps(previous) + "\n")
            try:
                self.runtime.start(slot, digest, state)
                self.runtime.wait_healthy(slot)
                atomic_write(self.config, configuration(slot, digest), 0o644)
                self.runtime.validate()
                self.runtime.reload()
                self.runtime.probe(digest)
                state = {"active": slot, "images": {**state["images"], slot: digest}}
                atomic_write(self.state_file, json.dumps(state) + "\n")
                self.journal.unlink()
            except BaseException:
                self.rollback()
                raise
            print(f"Deployed {digest} to {slot}")


def interrupted(signum, frame):
    raise RuntimeError(f"Deployment interrupted by signal {signum}")


def main():
    if len(sys.argv) != 2:
        raise ValueError("Usage: portfolio-deploy sha256:<digest>")
    validate_digest(sys.argv[1])
    if os.geteuid() != 0:
        raise PermissionError("Deployment helper requires root")
    os.umask(0o077)
    for signum in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
        signal.signal(signum, interrupted)
    Deployer(ROOT, DockerRuntime(ROOT)).deploy(sys.argv[1])


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)

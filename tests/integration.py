import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path


REPOSITORY = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("deploy", REPOSITORY / "bin" / "deploy.py")
DEPLOY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(DEPLOY)
PROJECT = "portfolio-test-" + uuid.uuid4().hex[:10]
DIGEST_A = "sha256:" + "a" * 64
DIGEST_B = "sha256:" + "b" * 64
DIGEST_C = "sha256:" + "c" * 64
IMAGE = sys.argv[1] if len(sys.argv) > 1 else "kozikowski-it:review"


class LocalRuntime(DEPLOY.DockerRuntime):
    fail_probe = False
    fail_health = False

    def run(self, arguments, env=None, timeout=180):
        result = subprocess.run([shutil.which("docker"), *arguments], env=dict(os.environ, **(env or {})),
                                text=True, capture_output=True, timeout=timeout)
        if result.returncode:
            raise RuntimeError(result.stderr)
        return result.stdout.strip()

    def compose(self, arguments, images=None):
        return self.run(["compose", "--project-name", PROJECT, "--project-directory", str(self.root),
                         "-f", str(self.root / "compose.yaml"), *arguments],
                        {"APP_BLUE_IMAGE": IMAGE, "APP_GREEN_IMAGE": IMAGE})

    def pull(self, digest):
        data = json.loads(self.run(["image", "inspect", IMAGE]))[0]
        assert data["Config"]["User"] in ("node", "node:node", "1000", "1000:1000")
        assert data["Config"]["Healthcheck"]["Test"][0] != "NONE"

    def wait_healthy(self, slot):
        if self.fail_health:
            self.run(["stop", self.container(f"app-{slot}")])
        return super().wait_healthy(slot)

    def probe(self, digest):
        if self.fail_probe and digest == DIGEST_C:
            self.run(["stop", self.container("app-blue")])
        return super().probe(digest)


def main():
    with tempfile.TemporaryDirectory(prefix="portfolio-integration-") as temporary:
        root = Path(temporary)
        (root / "state").mkdir()
        (root / "caddy").mkdir()
        shutil.copy(REPOSITORY / "caddy" / "Caddyfile", root / "caddy" / "Caddyfile")
        source = (REPOSITORY / "compose.yaml").read_text()
        source = source.replace('      - "80:80"\n      - "443:443"\n      - "443:443/udp"', '      - "127.0.0.1::8080"')
        (root / "compose.yaml").write_text(source)
        original_config = DEPLOY.configuration
        DEPLOY.configuration = lambda slot, digest: "{\n    auto_https off\n}\n\n" + original_config(slot, digest)
        (root / "caddy" / "Caddyfile").write_text("{\n    auto_https off\n}\n\n:8080 {\n    respond 503\n}\n")
        runtime = LocalRuntime(root)
        try:
            runtime.compose(["up", "-d", "--no-deps", "caddy"])
            deployer = DEPLOY.Deployer(root, runtime)
            deployer.deploy(DIGEST_A)
            first_id = runtime.container("app-blue")
            deployer.deploy(DIGEST_B)
            assert runtime.container("app-blue") == first_id
            runtime.probe(DIGEST_B)
            previous_state = deployer.state()
            previous_config = deployer.config.read_text()
            runtime.fail_probe = True
            try:
                deployer.deploy(DIGEST_C)
                raise AssertionError("Deployment unexpectedly succeeded after candidate was stopped")
            except RuntimeError:
                pass
            assert deployer.state() == previous_state
            assert deployer.config.read_text() == previous_config
            assert not deployer.journal.exists()
            runtime.fail_probe = False
            runtime.probe(DIGEST_B)
            runtime.fail_health = True
            try:
                deployer.deploy(DIGEST_C)
                raise AssertionError("Deployment unexpectedly succeeded with stopped candidate")
            except RuntimeError:
                pass
            assert deployer.state() == previous_state
            assert deployer.config.read_text() == previous_config
            runtime.probe(DIGEST_B)
            print("PASS: real Caddy reload, two slot rotation, previous-container retention, routed failure rollback and candidate health failure")
        finally:
            runtime.compose(["down", "--volumes", "--remove-orphans"])


if __name__ == "__main__":
    main()

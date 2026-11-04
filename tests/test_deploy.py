import importlib.util
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


MODULE = Path(__file__).resolve().parents[1] / "bin" / "deploy.py"
DIGEST_A = "sha256:" + "a" * 64
DIGEST_B = "sha256:" + "b" * 64


class FakeRuntime:
    def __init__(self):
        self.containers = {}
        self.failure = None
        self.reloads = 0

    def pull(self, digest):
        if self.failure == "pull":
            raise RuntimeError("pull failed")

    def start(self, slot, digest, state):
        self.containers[slot] = digest

    def wait_healthy(self, slot):
        if self.failure == "health":
            raise RuntimeError("health failed")

    def validate(self):
        if self.failure == "validate":
            raise RuntimeError("invalid config")

    def reload(self):
        self.reloads += 1
        if self.failure == "reload" and self.reloads == 2:
            raise RuntimeError("reload failed")

    def probe(self, digest):
        if self.failure == "probe" and digest == DIGEST_B:
            raise RuntimeError("routed health failed")


def load_module():
    spec = importlib.util.spec_from_file_location("deploy", MODULE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class DeployTests(unittest.TestCase):
    def test_deployment_module_exists(self):
        self.assertTrue(MODULE.exists(), "The validated deployment helper is missing")

    def test_portfolio_deploy_keeps_independent_demo_routes(self):
        module = load_module()
        self.assertIn('import sites/*.caddy', module.configuration('blue', DIGEST_A))

    def test_digest_validation(self):
        if not MODULE.exists():
            self.skipTest("Waiting for helper")
        module = load_module()
        self.assertEqual(module.validate_digest(DIGEST_A), DIGEST_A)
        for value in ["latest", "sha256:" + "A" * 64, DIGEST_A + "\n", DIGEST_A + ";id", "other@" + DIGEST_A, ""]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                module.validate_digest(value)

    def test_ssh_wrapper_rejects_shell_and_command_injection(self):
        wrapper = MODULE.parent / "portfolio-ssh"
        for command in ["", "sh", DIGEST_A, "deploy latest", " deploy " + DIGEST_A,
                        "deploy " + DIGEST_A + "\n", "deploy " + DIGEST_A + "; id",
                        "deploy " + DIGEST_A + " extra", "deploy $(id)",
                        "deploy sha256:" + "A" * 64, "deploy\t" + DIGEST_A]:
            with self.subTest(command=command):
                result = subprocess.run(["bash", str(wrapper)], capture_output=True, text=True,
                                        env=dict(os.environ, SSH_ORIGINAL_COMMAND=command))
                self.assertEqual(result.returncode, 64)
                self.assertIn("Only deploy", result.stderr)


@unittest.skipUnless(MODULE.exists(), "Waiting for helper")
class TransactionTests(unittest.TestCase):
    def setUp(self):
        self.module = load_module()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "state").mkdir()
        (self.root / "caddy").mkdir()
        (self.root / "caddy" / "Caddyfile").write_text("maintenance\n")
        self.runtime = FakeRuntime()
        self.deployer = self.module.Deployer(self.root, self.runtime)

    def state(self):
        return json.loads((self.root / "state" / "deployment.json").read_text())

    def test_two_successful_deploys_preserve_previous_container(self):
        self.deployer.deploy(DIGEST_A)
        self.deployer.deploy(DIGEST_B)
        self.assertEqual(self.state()["active"], "green")
        self.assertEqual(self.runtime.containers, {"blue": DIGEST_A, "green": DIGEST_B})
        self.assertIn("app-green:3000", (self.root / "caddy" / "Caddyfile").read_text())
        self.assertFalse((self.root / "state" / "pending.json").exists())

    def test_failures_preserve_previous_route_and_state(self):
        self.deployer.deploy(DIGEST_A)
        before = self.state()
        config = (self.root / "caddy" / "Caddyfile").read_text()
        for failure in ["pull", "health", "validate", "reload", "probe"]:
            with self.subTest(failure=failure):
                self.runtime.failure = failure
                self.runtime.reloads = 1
                with self.assertRaises(RuntimeError):
                    self.deployer.deploy(DIGEST_B)
                self.assertEqual(self.state(), before)
                self.assertEqual((self.root / "caddy" / "Caddyfile").read_text(), config)
                self.assertEqual(self.runtime.containers["blue"], DIGEST_A)

    def test_interrupted_transaction_restores_old_route_before_new_deploy(self):
        self.deployer.deploy(DIGEST_A)
        old = self.state()
        config = (self.root / "caddy" / "Caddyfile").read_text()
        (self.root / "state" / "pending.json").write_text(json.dumps({"state": old, "config": config}))
        (self.root / "caddy" / "Caddyfile").write_text("broken\n")
        self.runtime.failure = "pull"
        with self.assertRaises(RuntimeError):
            self.deployer.deploy(DIGEST_B)
        self.assertEqual((self.root / "caddy" / "Caddyfile").read_text(), config)
        self.assertEqual(self.state(), old)
        self.assertFalse((self.root / "state" / "pending.json").exists())

    def test_lock_rejects_concurrent_deploy(self):
        with (self.root / "state" / "deploy.lock").open("a") as lock:
            self.module.fcntl.flock(lock, self.module.fcntl.LOCK_EX | self.module.fcntl.LOCK_NB)
            with self.assertRaisesRegex(RuntimeError, "already running"):
                self.deployer.deploy(DIGEST_A)
        self.assertEqual(self.runtime.containers, {})

    def test_failed_initial_deploy_restores_maintenance(self):
        self.runtime.failure = "health"
        with self.assertRaises(RuntimeError):
            self.deployer.deploy(DIGEST_A)
        self.assertEqual((self.root / "caddy" / "Caddyfile").read_text(), "maintenance\n")
        self.assertIsNone(self.state()["active"])

    def test_failed_rollback_keeps_recovery_journal(self):
        self.deployer.deploy(DIGEST_A)
        self.runtime.failure = "probe"
        original_reload = self.runtime.reload
        def fail_rollback():
            if self.runtime.reloads >= 2:
                raise RuntimeError("Caddy unavailable during rollback")
            return original_reload()
        self.runtime.reload = fail_rollback
        with self.assertRaisesRegex(RuntimeError, "rollback"):
            self.deployer.deploy(DIGEST_B)
        self.assertTrue(self.deployer.journal.exists())
        self.assertEqual(self.state()["active"], "blue")


if __name__ == "__main__":
    unittest.main()

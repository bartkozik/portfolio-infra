import importlib.util
import os
from pathlib import Path
import subprocess
import unittest
from test_deploy import TransactionTests, DIGEST_A, DIGEST_B

spec = importlib.util.spec_from_file_location('epoca_deploy', Path(__file__).resolve().parents[1] / 'bin' / 'epoca_deploy.py')
epoca = importlib.util.module_from_spec(spec)
spec.loader.exec_module(epoca)


class EpocaTests(unittest.TestCase):
    def test_pair_identity_changes_when_either_image_changes(self):
        first = epoca.release_identifier({'api': DIGEST_A, 'gateway': DIGEST_A, 'web': DIGEST_B})
        self.assertNotEqual(first, epoca.release_identifier({'api': DIGEST_B, 'gateway': DIGEST_A, 'web': DIGEST_B}))
        self.assertNotEqual(first, epoca.release_identifier({'api': DIGEST_A, 'gateway': DIGEST_A, 'web': DIGEST_A}))
        self.assertEqual(first, epoca.release_identifier({'web': DIGEST_B, 'gateway': DIGEST_A, 'api': DIGEST_A}))

    def test_invalid_digest_is_rejected(self):
        with self.assertRaises(ValueError):
            epoca.release_identifier({'api': 'latest', 'gateway': DIGEST_A, 'web': DIGEST_A})

    def test_routes_use_the_same_slot_and_do_not_expose_sync_or_mcp(self):
        config = epoca.configuration('green', DIGEST_A)
        self.assertIn('epoca-gateway-green:8080', config)
        self.assertIn('epoca-web-green:8080', config)
        self.assertNotIn('blue', config)
        self.assertNotIn('1234', config)
        self.assertNotIn('mcp', config)
        self.assertNotIn('epoca-api-', config)
        self.assertIn('header_up X-Real-IP {remote_host}', config)
        self.assertIn('max_size 32KB', config)

    def test_ssh_wrapper_rejects_extra_arguments_and_shell_commands(self):
        wrapper = Path(__file__).resolve().parents[1] / 'bin' / 'epoca-ssh'
        for command in ('', 'sh', f'deploy {DIGEST_A}', f'deploy {DIGEST_A} {DIGEST_B} {DIGEST_A};id',
                        f'deploy {DIGEST_A} {DIGEST_B} {DIGEST_A}\n', f'deploy {DIGEST_A} {DIGEST_B} {DIGEST_A} extra', f'deploy web {DIGEST_A};id', f'deploy backend {DIGEST_A}'):
            with self.subTest(command=command):
                result = subprocess.run(['bash', str(wrapper)], capture_output=True,
                                        env=dict(os.environ, SSH_ORIGINAL_COMMAND=command))
                self.assertEqual(result.returncode, 64)


class EpocaTransactions(TransactionTests):
    def setUp(self):
        super().setUp()
        self.deployer.configuration = epoca.configuration

    def test_two_successful_deploys_preserve_previous_container(self):
        self.deployer.deploy(DIGEST_A)
        self.deployer.deploy(DIGEST_B)
        self.assertEqual(self.state()['active'], 'green')
        self.assertIn('epoca-gateway-green:8080', self.deployer.config.read_text())
        self.assertIn('epoca-web-green:8080', self.deployer.config.read_text())

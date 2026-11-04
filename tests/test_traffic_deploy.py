import importlib.util
import os
from pathlib import Path
import subprocess
import unittest
from test_deploy import TransactionTests, DIGEST_A, DIGEST_B

spec = importlib.util.spec_from_file_location('traffic_deploy', Path(__file__).resolve().parents[1] / 'bin' / 'traffic_deploy.py')
traffic = importlib.util.module_from_spec(spec)
spec.loader.exec_module(traffic)


class TrafficTests(unittest.TestCase):
    def test_pair_identity_changes_when_either_image_changes(self):
        first = traffic.release_identifier({'api': DIGEST_A, 'web': DIGEST_B})
        self.assertNotEqual(first, traffic.release_identifier({'api': DIGEST_B, 'web': DIGEST_B}))
        self.assertNotEqual(first, traffic.release_identifier({'api': DIGEST_A, 'web': DIGEST_A}))
        self.assertEqual(first, traffic.release_identifier({'web': DIGEST_B, 'api': DIGEST_A}))

    def test_invalid_digest_is_rejected(self):
        with self.assertRaises(ValueError):
            traffic.release_identifier({'api': 'latest', 'web': DIGEST_A})

    def test_routes_use_the_same_slot_and_do_not_expose_sync_or_mcp(self):
        config = traffic.configuration('green', DIGEST_A)
        self.assertIn('traffic-api-green:4000', config)
        self.assertIn('traffic-web-green:8080', config)
        self.assertNotIn('blue', config)
        self.assertNotIn('1234', config)
        self.assertNotIn('mcp', config)

    def test_ssh_wrapper_rejects_extra_arguments_and_shell_commands(self):
        wrapper = Path(__file__).resolve().parents[1] / 'bin' / 'traffic-ssh'
        for command in ('', 'sh', f'deploy {DIGEST_A}', f'deploy {DIGEST_A} {DIGEST_B};id',
                        f'deploy {DIGEST_A} {DIGEST_B}\n', f'deploy {DIGEST_A} {DIGEST_B} extra'):
            with self.subTest(command=command):
                result = subprocess.run(['bash', str(wrapper)], capture_output=True,
                                        env=dict(os.environ, SSH_ORIGINAL_COMMAND=command))
                self.assertEqual(result.returncode, 64)


class TrafficTransactions(TransactionTests):
    def setUp(self):
        super().setUp()
        self.deployer.configuration = traffic.configuration

    def test_two_successful_deploys_preserve_previous_container(self):
        self.deployer.deploy(DIGEST_A)
        self.deployer.deploy(DIGEST_B)
        self.assertEqual(self.state()['active'], 'green')
        self.assertIn('traffic-api-green:4000', self.deployer.config.read_text())
        self.assertIn('traffic-web-green:8080', self.deployer.config.read_text())

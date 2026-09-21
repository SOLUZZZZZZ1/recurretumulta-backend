import importlib.util
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

script = Path(__file__).resolve().parents[1] / "scripts/rtm_local_integration_checks.py"
if not script.exists():
    script = Path(__file__).with_name("rtm_local_integration_checks.py")
spec = importlib.util.spec_from_file_location("rtm_local_integration_checks_tested", script)
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


class LocalIntegrationBoundaryTest(unittest.TestCase):
    def test_inherited_database_and_external_credentials_are_not_forwarded(self):
        env = runner.clean_environment({
            "PATH": "tools", "SystemRoot": "Windows", "DATABASE_URL": "do-not-forward",
            "PGSERVICE": "live-service", "PGPASSWORD": "do-not-forward",
            "OPENAI_API_KEY": "do-not-forward", "STRIPE_SECRET_KEY": "do-not-forward",
            "RTM_ENABLE_LOCAL_OPERATOR_AUTH": "1", "PYTHONPATH": "untrusted-modules",
            "RTM_ENABLE_B2": "1", "RTM_ENABLE_EXTERNAL_SUBMISSION": "1",
        })
        self.assertEqual(env["PATH"], "tools")
        for key in ("DATABASE_URL", "PGSERVICE", "PGPASSWORD", "OPENAI_API_KEY",
                    "STRIPE_SECRET_KEY", "RTM_ENABLE_LOCAL_OPERATOR_AUTH", "PYTHONPATH"):
            self.assertNotIn(key, env)
        self.assertEqual(env["RTM_ENABLE_B2"], "0")
        self.assertEqual(env["RTM_ENABLE_EXTERNAL_SUBMISSION"], "0")

    def test_server_must_match_every_coordinate_of_the_owned_cluster(self):
        folder = Path("owned-test-cluster").resolve()
        row = ["rtm_check_demo", "rtm_check_user", "127.0.0.1", 55000, str(folder)]
        options = dict(folder=folder, database=row[0], role=row[1], port=row[3])
        runner.verify_identity(row, **options)
        for index, replacement in enumerate(("rtm_local", "rtm_local_app", "192.0.2.1", 5432,
                                              str(folder.parent / "another-cluster"))):
            with self.subTest(index=index):
                changed = row.copy()
                changed[index] = replacement
                with self.assertRaises(RuntimeError):
                    runner.verify_identity(changed, **options)

    def test_normal_database_and_standard_port_are_refused_even_if_server_matches(self):
        folder = Path("owned-test-cluster").resolve()
        for database, role, port in (("rtm_local", "rtm_check_user", 55000),
                                     ("rtm_check_demo", "rtm_local_app", 55000),
                                     ("rtm_check_demo", "rtm_check_user", 5432)):
            with self.subTest(database=database, role=role, port=port):
                with self.assertRaises(RuntimeError):
                    runner.verify_identity((database, role, "127.0.0.1", port, str(folder)),
                                           folder=folder, database=database, role=role, port=port)

    def test_network_hook_refuses_other_services_and_external_dns(self):
        with patch.object(sys, "addaudithook") as install:
            runner.network_guard(55000)
        audit = install.call_args.args[0]
        audit("socket.connect", (None, ("127.0.0.1", 55000)))
        audit("socket.getaddrinfo", ("127.0.0.1", 55000))
        for address in (("127.0.0.1", 5432), ("127.0.0.1", 8000), ("192.0.2.1", 55000)):
            with self.subTest(address=address):
                with self.assertRaises(RuntimeError):
                    audit("socket.connect", (None, address))
        with self.assertRaises(RuntimeError):
            audit("socket.getaddrinfo", ("api.example.com", 443))


if __name__ == "__main__":
    unittest.main()

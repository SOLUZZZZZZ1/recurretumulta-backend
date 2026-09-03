from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CHECKOUT_V7_SHA = "3d3c42e5aac5ba805825da76410c181273ba90b1"
SETUP_PYTHON_V7_SHA = "5fda3b95a4ea91299a34e894583c3862153e4b97"


class DependencyAndCISecurityContractTest(unittest.TestCase):
    def test_security_sensitive_dependencies_are_fixed_versions(self) -> None:
        requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")
        expected = {
            "fastapi": "0.135.4",
            "starlette": "1.3.1",
            "python-multipart": "0.0.32",
            "requests": "2.34.2",
            "pypdf": "6.16.2",
            "pillow": "12.3.0",
        }

        for package, version in expected.items():
            self.assertRegex(
                requirements,
                rf"(?m)^{re.escape(package)}=={re.escape(version)}$",
            )

    def test_actions_are_commit_pinned_and_checkout_drops_credentials(self) -> None:
        workflows = tuple((ROOT / ".github" / "workflows").glob("*.yml"))
        self.assertTrue(workflows)

        for workflow in workflows:
            source = workflow.read_text(encoding="utf-8")
            with self.subTest(workflow=workflow.name):
                self.assertEqual(source.count("runs-on: ubuntu-24.04"), 1)
                self.assertNotIn("ubuntu-latest", source)
                self.assertNotRegex(
                    source,
                    r"uses:\s+actions/(?:checkout|setup-python)@v\d+\s*$",
                )
                action_refs = re.findall(
                    r"uses:\s+actions/(?:checkout|setup-python)@([0-9a-f]{40})",
                    source,
                )
                self.assertGreaterEqual(len(action_refs), 2)
                self.assertIn(
                    f"uses: actions/checkout@{CHECKOUT_V7_SHA}", source
                )
                self.assertIn(
                    f"uses: actions/setup-python@{SETUP_PYTHON_V7_SHA}", source
                )
                self.assertEqual(source.count("persist-credentials: false"), 1)
                self.assertEqual(source.count('PIP_ONLY_BINARY: ":all:"'), 1)

    def test_live_workflow_does_not_expose_secret_to_setup_steps(self) -> None:
        workflow = (
            ROOT / ".github" / "workflows" / "rtm-staging-synthetic-live.yml"
        ).read_text(encoding="utf-8")
        job_header, _ = workflow.split("    steps:", maxsplit=1)
        run_blocks = re.findall(
            r"(?ms)^ {8}run:\s*\|\n((?:^ {10,}.*\n?)*)",
            workflow,
        )

        self.assertNotIn("OPENAI_API_KEY", job_header)
        self.assertTrue(run_blocks)
        self.assertTrue(
            all("${{ inputs." not in block for block in run_blocks)
        )
        self.assertIn('--services "$RTM_STAGING_SERVICES"', workflow)
        self.assertEqual(
            workflow.count("OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}"),
            2,
        )

    def test_live_workflow_never_injects_secrets_into_dispatched_branch_code(self) -> None:
        workflow = (
            ROOT / ".github" / "workflows" / "rtm-staging-synthetic-live.yml"
        ).read_text(encoding="utf-8")

        self.assertRegex(
            workflow,
            re.compile(
                r"(?m)^ {4}if: \$\{\{ inputs[.]confirmation == "
                r"'SYNTHETIC_ONLY' && github[.]ref == 'refs/heads/main' \}\}$"
            ),
        )
        self.assertNotIn("github.head_ref", workflow)

    def test_security_branch_runs_core_ci(self) -> None:
        workflow = (
            ROOT / ".github" / "workflows" / "rtm-core-ci.yml"
        ).read_text(encoding="utf-8")
        self.assertIn('"rtm-ai-security-hardening-*"', workflow)
        self.assertIn("pip-audit==2.10.1", workflow)
        self.assertIn("python -m pip_audit --strict", workflow)
        self.assertIn("python scripts/rtm_secret_scan.py --history", workflow)


if __name__ == "__main__":
    unittest.main()

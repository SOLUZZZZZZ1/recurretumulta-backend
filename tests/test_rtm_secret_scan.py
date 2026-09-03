from __future__ import annotations

import base64
import contextlib
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from scripts import rtm_secret_scan


ROOT = Path(__file__).resolve().parents[1]


def _part(payload: dict[str, object]) -> str:
    encoded = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":")).encode("utf-8")
    )
    return encoded.rstrip(b"=").decode("ascii")


class SecretScanTest(unittest.TestCase):
    def test_detects_supported_secret_signatures_without_retaining_values(self) -> None:
        signed_token = ".".join(
            (
                _part({"alg": "HS256", "typ": "JWT"}),
                _part({"sub": "operator", "exp": 4102444800}),
                base64.urlsafe_b64encode(bytes(range(32))).rstrip(b"=").decode(),
            )
        )
        samples = {
            "pem_private_key": "-----BEGIN " + "PRIVATE KEY-----",
            "github_token": "gh" + "p_" + "Ab1" * 12,
            "openai_api_key": "sk-" + "Ab9_" * 8,
            "stripe_secret_key": "sk_" + "live_" + "Ab9" * 8,
            "aws_access_key_id": "AK" + "IA" + "1A2B3C4D5E6F7G8H",
            "aws_secret_access_key": (
                "aws_" + "secret_access_key=" + "Ab1/" * 10
            ),
            "slack_token": "xox" + "b-" + "Ab1-" * 8,
            "sendgrid_api_key": "S" + "G." + "A1b2" * 5 + "." + "C3d4" * 5,
            "google_api_key": "AI" + "za" + "Ab1_" * 8 + "XYZ",
            "assigned_application_secret": (
                "ADMIN_" + "TOKEN=" + "Q7vK2mP9xR4tW8yN3cL6zB1hF5sD0aJ"
            ),
            "signed_jwt": signed_token,
            "bearer_credential": (
                "Bearer "
                + base64.urlsafe_b64encode(bytes(range(48))).decode("ascii")
            ),
            "basic_auth_credential": (
                "Basic "
                + base64.b64encode(b"operator:private-password").decode("ascii")
            ),
            "url_with_userinfo": (
                "https" + "://operator:private-password@api.invalid"
            ),
        }

        for expected_kind, sample in samples.items():
            with self.subTest(kind=expected_kind):
                findings = rtm_secret_scan.scan_text(sample, path="sample.txt")
                self.assertIn(expected_kind, {item.kind for item in findings})
                self.assertTrue(all(sample not in repr(item) for item in findings))

    def test_placeholders_and_unsigned_jwt_are_not_credentials(self) -> None:
        unsigned = ".".join(
            (
                _part({"alg": "none", "typ": "JWT"}),
                _part({"sub": "example"}),
                "placeholderSignature12345",
            )
        )
        text = "\n".join(
            (
                "Bearer opaque.placeholder.token.value.with.more.characters",
                "Bearer " + "a" * 64,
                unsigned,
            )
        )
        self.assertEqual(rtm_secret_scan.scan_text(text, path="sample.txt"), [])

    def test_binary_file_is_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fixture.bin"
            path.write_bytes(b"\x00\xff" + (b"secret" * 20))
            self.assertEqual(
                rtm_secret_scan.scan_file(path, display_path="fixture.bin"),
                [],
            )

    def test_fixture_allowlist_is_bound_to_exact_value_and_path(self) -> None:
        allowed_value = (
            "https" + "://user:pass@good.example"
        )
        changed_value = (
            "https" + "://user:different-password@good.example"
        )
        fixture_path = "tests/test_rtm_http_security.py"

        self.assertEqual(
            rtm_secret_scan.scan_text(allowed_value, path=fixture_path),
            [],
        )
        findings = rtm_secret_scan.scan_text(changed_value, path=fixture_path)
        self.assertEqual([item.kind for item in findings], ["url_with_userinfo"])

    def test_cli_scans_tracked_and_untracked_files_without_printing_secret(self) -> None:
        tracked_secret = "gh" + "p_" + "Ab1" * 12
        untracked_secret = "gh" + "p_" + "Cd2" * 12
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(
                ["git", "init", "--quiet", str(root)],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            (root / "tracked.txt").write_text(tracked_secret, encoding="utf-8")
            (root / "untracked.txt").write_text(untracked_secret, encoding="utf-8")
            subprocess.run(
                ["git", "-C", str(root), "add", "tracked.txt"],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                exit_code = rtm_secret_scan.main(["--root", str(root)])

        output = stderr.getvalue()
        self.assertEqual(exit_code, 1)
        self.assertIn("tracked.txt:1:github_token", output)
        self.assertIn("untracked.txt:1:github_token", output)
        self.assertNotIn(tracked_secret, output)
        self.assertNotIn(untracked_secret, output)

    def test_history_scan_detects_a_secret_removed_from_the_current_tree(self) -> None:
        retired_secret = "gh" + "p_" + "Z7q" * 12
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(
                ["git", "init", "--quiet", str(root)],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            for name, value in (
                ("user.email", "security-test@invalid.example"),
                ("user.name", "RTM Security Test"),
            ):
                subprocess.run(
                    ["git", "-C", str(root), "config", name, value],
                    check=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            retired = root / "retired.txt"
            retired.write_text(retired_secret, encoding="utf-8")
            subprocess.run(
                ["git", "-C", str(root), "add", "retired.txt"],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            subprocess.run(
                ["git", "-C", str(root), "commit", "--quiet", "-m", "fixture"],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            retired.write_text("safe\n", encoding="utf-8")
            subprocess.run(
                ["git", "-C", str(root), "commit", "--quiet", "-am", "remove"],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

            self.assertEqual(rtm_secret_scan.scan_repository(root), [])
            findings = rtm_secret_scan.scan_history(root)

        self.assertIn("github_token", {finding.kind for finding in findings})
        self.assertTrue(all(retired_secret not in repr(item) for item in findings))

    def test_history_scan_covers_commit_and_annotated_tag_messages(self) -> None:
        commit_secret = "gh" + "p_" + "J8w" * 12
        tag_secret = "gh" + "p_" + "K9x" * 12
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(
                ["git", "init", "--quiet", str(root)],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            for name, value in (
                ("user.email", "security-test@invalid.example"),
                ("user.name", "RTM Security Test"),
            ):
                subprocess.run(
                    ["git", "-C", str(root), "config", name, value],
                    check=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            (root / "safe.txt").write_text("safe\n", encoding="utf-8")
            subprocess.run(
                ["git", "-C", str(root), "add", "safe.txt"],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            subprocess.run(
                ["git", "-C", str(root), "commit", "--quiet", "-F", "-"],
                input=f"commit fixture {commit_secret}\n".encode("utf-8"),
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            subprocess.run(
                ["git", "-C", str(root), "tag", "-a", "fixture-tag", "-F", "-"],
                input=f"tag fixture {tag_secret}\n".encode("utf-8"),
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            tree = subprocess.run(
                ["git", "-C", str(root), "rev-parse", "HEAD^{tree}"],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            ).stdout.decode("ascii").strip()
            binary_commit = subprocess.run(
                ["git", "-C", str(root), "commit-tree", tree],
                input=(b"\x01" * 128) + b"\n",
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            ).stdout.decode("ascii").strip()
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(root),
                    "update-ref",
                    "refs/heads/binary-message",
                    binary_commit,
                ],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

            self.assertEqual(rtm_secret_scan.scan_repository(root), [])
            findings = rtm_secret_scan.scan_history(root)
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                exit_code = rtm_secret_scan.main(
                    ["--root", str(root), "--history"]
                )

        self.assertTrue(
            any(
                finding.path.endswith("/commit-message")
                and finding.kind == "github_token"
                for finding in findings
            )
        )
        self.assertTrue(
            any(
                finding.path.endswith("/tag-message")
                and finding.kind == "github_token"
                for finding in findings
            )
        )
        self.assertIn(
            "binary_repository_history_message",
            {finding.kind for finding in findings},
        )
        self.assertTrue(all(commit_secret not in repr(item) for item in findings))
        self.assertTrue(all(tag_secret not in repr(item) for item in findings))
        output = stderr.getvalue()
        self.assertEqual(exit_code, 1)
        self.assertIn("commit-message", output)
        self.assertIn("tag-message", output)
        self.assertNotIn(commit_secret, output)
        self.assertNotIn(tag_secret, output)

    def test_current_tracked_tree_has_no_credential_signature(self) -> None:
        self.assertEqual(rtm_secret_scan.scan_repository(ROOT), [])

    def test_scanner_and_its_tests_do_not_trigger_their_own_rules(self) -> None:
        paths = (
            ROOT / "scripts" / "rtm_secret_scan.py",
            ROOT / "tests" / "test_rtm_secret_scan.py",
        )
        for path in paths:
            with self.subTest(path=path.name):
                self.assertEqual(
                    rtm_secret_scan.scan_file(path, display_path=str(path.name)),
                    [],
                )

    def test_core_ci_runs_scanner_before_dependency_installation(self) -> None:
        workflow = (
            ROOT / ".github" / "workflows" / "rtm-core-ci.yml"
        ).read_text(encoding="utf-8")
        scanner = "run: python scripts/rtm_secret_scan.py --history"
        self.assertIn(scanner, workflow)
        self.assertLess(workflow.index(scanner), workflow.index("Install dependencies"))


if __name__ == "__main__":
    unittest.main()

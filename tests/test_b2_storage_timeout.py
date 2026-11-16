import unittest
from unittest.mock import patch

import b2_storage


class B2StorageTimeoutTests(unittest.TestCase):
    def setUp(self):
        self.environment = {
            "B2_ENDPOINT": "https://s3.us-west-000.backblazeb2.com",
            "B2_KEY_ID": "test-id",
            "B2_APPLICATION_KEY": "test-key",
        }

    def test_bounded_client_uses_connection_and_read_timeouts_without_retries(self):
        with patch.object(b2_storage, "_env", side_effect=self.environment.__getitem__), \
             patch.object(b2_storage.boto3, "client") as client:
            b2_storage.get_s3_client(request_timeout_seconds=5)

        config = client.call_args.kwargs["config"]
        self.assertEqual(config.connect_timeout, 5)
        self.assertEqual(config.read_timeout, 5)
        self.assertEqual(config.retries, {"total_max_attempts": 1})

    def test_default_client_configuration_is_unchanged(self):
        with patch.object(b2_storage, "_env", side_effect=self.environment.__getitem__), \
             patch.object(b2_storage.boto3, "client") as client:
            b2_storage.get_s3_client()

        config = client.call_args.kwargs["config"]
        for key in ("connect_timeout", "read_timeout", "retries"):
            self.assertNotIn(key, config._user_provided_options)
        self.assertEqual(config.signature_version, "s3v4")
        self.assertEqual(config.s3, {"addressing_style": "path"})

    def test_invalid_timeout_is_rejected(self):
        for value in (0, -1, 6, True, "5"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                b2_storage.get_s3_client(request_timeout_seconds=value)


if __name__ == "__main__":
    unittest.main()


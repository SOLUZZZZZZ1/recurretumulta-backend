from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

import b2_storage


class _S3:
    def __init__(self):
        self.kwargs = None
        self.deleted = []

    def generate_presigned_url(self, **kwargs):
        self.kwargs = kwargs
        return "https://storage.invalid/signed"

    def delete_object(self, **kwargs):
        self.deleted.append(kwargs)


class _FailingPutS3(_S3):
    def put_object(self, **_kwargs):
        raise TimeoutError("ack lost")


class B2StorageSecurityTest(unittest.TestCase):
    def test_read_timeout_is_opt_in_and_leaves_default_client_configuration_unchanged(self):
        configuration = {
            "B2_ENDPOINT": "https://s3.us-west-000.backblazeb2.com",
            "B2_KEY_ID": "test-id", "B2_APPLICATION_KEY": "test-key",
        }
        with (
            patch.object(b2_storage, "require_capability") as capability,
            patch.object(b2_storage._local_storage, "local_document_storage_requested", return_value=False),
            patch.object(b2_storage, "_env", side_effect=configuration.__getitem__),
            patch.object(b2_storage.boto3, "client") as client,
        ):
            b2_storage.get_s3_client(request_timeout_seconds=5)
            bounded = client.call_args.kwargs["config"]
            self.assertEqual(bounded.connect_timeout, 5)
            self.assertEqual(bounded.read_timeout, 5)
            self.assertEqual(bounded.retries, {"total_max_attempts": 1})
            b2_storage.get_s3_client()
            default = client.call_args.kwargs["config"]
            for key in ("connect_timeout", "read_timeout", "retries"):
                self.assertNotIn(key, default._user_provided_options)
            self.assertEqual(default.signature_version, "s3v4")
            self.assertEqual(default.s3, {"addressing_style": "path"})
            self.assertEqual(capability.call_count, 2)
            capability.assert_called_with("b2")

    def test_download_only_passes_timeout_when_requested_and_preserves_stream_limit(self):
        for timeout in (None, 5):
            with self.subTest(timeout=timeout):
                body = Mock()
                body.read.return_value = b"pdf"
                client = Mock()
                client.get_object.return_value = {"Body": body}
                with (
                    patch.object(b2_storage, "local_document_storage_enabled", return_value=False),
                    patch.object(b2_storage, "get_b2_bucket", return_value="private-bucket"),
                    patch.object(b2_storage, "get_s3_client", return_value=client) as factory,
                ):
                    self.assertEqual(b2_storage.download_bytes_limited(
                        "private-bucket", "cases/case-1/original/object.pdf", max_bytes=10,
                        case_id="case-1", **({"request_timeout_seconds": timeout} if timeout is not None else {}),
                    ), b"pdf")
                if timeout is None:
                    factory.assert_called_once_with()
                else:
                    factory.assert_called_once_with(request_timeout_seconds=5)
                client.get_object.assert_called_once_with(Bucket="private-bucket", Key="cases/case-1/original/object.pdf")
                body.read.assert_called_once_with(11)
                body.close.assert_called_once_with()

    def test_invalid_timeouts_are_rejected_before_credentials_or_client_construction(self):
        for value in (0, -1, 5.1, float("inf"), float("nan"), True, "5"):
            with self.subTest(value=value), patch.object(b2_storage, "_env") as environment, \
                 patch.object(b2_storage.boto3, "client") as client, \
                 patch.object(b2_storage, "local_document_storage_enabled") as local:
                with self.assertRaises(ValueError):
                    b2_storage.get_s3_client(request_timeout_seconds=value)
                with self.assertRaises(ValueError):
                    b2_storage.download_bytes_limited("private-bucket", "cases/case-1/original/object.pdf",
                        max_bytes=10, case_id="case-1", request_timeout_seconds=value)
                environment.assert_not_called()
                client.assert_not_called()
                local.assert_not_called()

    def test_timed_out_read_closes_the_stream_without_retrying(self):
        body = Mock()
        body.read.side_effect = TimeoutError("read timeout")
        client = Mock()
        client.get_object.return_value = {"Body": body}
        with (
            patch.object(b2_storage, "local_document_storage_enabled", return_value=False),
            patch.object(b2_storage, "get_b2_bucket", return_value="private-bucket"),
            patch.object(b2_storage, "get_s3_client", return_value=client),
        ):
            with self.assertRaises(TimeoutError):
                b2_storage.download_bytes_limited("private-bucket", "cases/case-1/original/object.pdf",
                    max_bytes=10, case_id="case-1", request_timeout_seconds=5)
        self.assertEqual(client.get_object.call_count, 1)
        body.read.assert_called_once_with(11)
        body.close.assert_called_once_with()

    def test_presign_forces_safe_attachment_and_binary_content_type(self):
        client = _S3()
        with (
            patch.object(b2_storage, "get_b2_bucket", return_value="private-bucket"),
            patch.object(b2_storage, "get_s3_client", return_value=client),
        ):
            url = b2_storage.presign_get_url(
                "private-bucket",
                "cases/one/original/object.pdf",
                filename='evil"\r\nContent-Type_text_html.pdf',
            )
        self.assertEqual(url, "https://storage.invalid/signed")
        params = client.kwargs["Params"]
        self.assertEqual(params["ResponseContentType"], "application/octet-stream")
        self.assertTrue(params["ResponseContentDisposition"].startswith("attachment;"))
        self.assertNotIn("\r", params["ResponseContentDisposition"])
        self.assertNotIn("\n", params["ResponseContentDisposition"])
        self.assertNotIn('evil"', params["ResponseContentDisposition"])

    def test_endpoint_is_exact_backblaze_https_origin(self):
        self.assertEqual(
            b2_storage._validated_b2_endpoint(
                "https://s3.us-west-000.backblazeb2.com/"
            ),
            "https://s3.us-west-000.backblazeb2.com",
        )
        for endpoint in (
            "http://s3.us-west-000.backblazeb2.com",
            "https://attacker.example",
            "https://s3.us-west-000.backblazeb2.com.attacker.example",
            "https://user" + ":pass@s3.us-west-000.backblazeb2.com",
            "https://s3.us-west-000.backblazeb2.com/path",
        ):
            with self.subTest(endpoint=endpoint):
                with self.assertRaises(RuntimeError):
                    b2_storage._validated_b2_endpoint(endpoint)

    def test_read_coordinate_can_be_bound_to_exact_case(self):
        with patch.object(b2_storage, "get_b2_bucket", return_value="private-bucket"):
            self.assertEqual(
                b2_storage.validate_b2_object_coordinate(
                    "private-bucket",
                    "cases/case-1/original/object.pdf",
                    case_id="case-1",
                ),
                ("private-bucket", "cases/case-1/original/object.pdf"),
            )
            with self.assertRaises(ValueError):
                b2_storage.validate_b2_object_coordinate(
                    "private-bucket",
                    "cases/case-2/original/object.pdf",
                    case_id="case-1",
                )

    def test_upload_attempts_cleanup_when_put_ack_is_ambiguous(self):
        client = _FailingPutS3()
        with (
            patch.object(b2_storage, "get_b2_bucket", return_value="private-bucket"),
            patch.object(b2_storage, "get_s3_client", return_value=client),
            patch.object(b2_storage.uuid, "uuid4") as generated,
        ):
            generated.return_value.hex = "a" * 32
            with self.assertRaises(TimeoutError):
                b2_storage.upload_bytes(
                    "00000000-0000-0000-0000-000000000001",
                    "original",
                    b"payload",
                    ".pdf",
                    "application/pdf",
                )

        self.assertEqual(
            client.deleted,
            [{
                "Bucket": "private-bucket",
                "Key": (
                    "cases/00000000-0000-0000-0000-000000000001/"
                    f"original/{'a' * 32}.pdf"
                ),
            }],
        )

    def test_delete_object_rejects_coordinates_outside_rtm_namespace(self):
        client = _S3()
        with (
            patch.object(b2_storage, "get_b2_bucket", return_value="private-bucket"),
            patch.object(b2_storage, "get_s3_client", return_value=client),
        ):
            with self.assertRaises(ValueError):
                b2_storage.delete_object(
                    "other-bucket",
                    "cases/case/original/object.pdf",
                )
            with self.assertRaises(ValueError):
                b2_storage.delete_object(
                    "private-bucket",
                    "cases/case/../object.pdf",
                )
        self.assertEqual(client.deleted, [])


if __name__ == "__main__":
    unittest.main()

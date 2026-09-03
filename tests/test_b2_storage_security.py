from __future__ import annotations

import unittest
from unittest.mock import patch

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

from __future__ import annotations

import hashlib
import ctypes
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from unittest.mock import Mock
import uuid

from fastapi import HTTPException

import b2_storage
from rtm_core import local_document_storage as storage
from rtm_core.local_operator_auth import LocalOperatorAuthMisconfigured
from rtm_core.runtime_capabilities import CapabilityDisabledError, capability_state
from tests.test_rtm_local_operator_auth import local_environment


CASE = "00000000-0000-0000-0000-000000000001"
OTHER_CASE = "00000000-0000-0000-0000-000000000002"


class LocalDocumentStorageTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "documents"
        self.root.mkdir()
        self.environ = local_environment() | {
            "RTM_ENABLE_LOCAL_DOCUMENT_STORAGE": "1",
            "RTM_LOCAL_DOCUMENT_ROOT": str(self.root),
        }
        self.environment_patch = patch.dict(os.environ, self.environ, clear=True)
        self.environment_patch.start()
        self.addCleanup(self.environment_patch.stop)

    def upload(self, data=b"synthetic document bytes"):
        return b2_storage.upload_bytes(CASE, "original", data, ".pdf", "application/pdf")

    def test_roundtrip_has_distinct_coordinates_size_hash_and_no_network(self):
        data = b"synthetic local document"
        with patch.object(b2_storage.boto3, "client") as client:
            self.assertEqual(b2_storage.require_http_document_storage(), "local")
            bucket, key = self.upload(data)
            self.assertEqual(bucket, storage.LOCAL_DOCUMENT_BUCKET)
            self.assertEqual(b2_storage.get_document_bucket(), bucket)
            self.assertEqual(b2_storage.get_b2_bucket(), bucket)
            self.assertEqual(b2_storage.download_bytes(bucket, key, case_id=CASE), data)
            self.assertEqual(b2_storage.download_bytes_limited(bucket, key, max_bytes=len(data)), data)
            self.assertFalse(capability_state("b2").enabled)
            with self.assertRaises(CapabilityDisabledError):
                b2_storage.get_s3_client()
            client.assert_not_called()
        persisted = self.root.joinpath(key).read_bytes()
        magic, size, digest = storage._HEADER.unpack(persisted[:storage._HEADER.size])
        self.assertEqual(magic, storage._MAGIC)
        self.assertEqual(size, len(data))
        self.assertEqual(digest, hashlib.sha256(data).digest())
        self.assertEqual(persisted[storage._HEADER.size:], data)

    def test_false_flag_restores_b2_guard_but_never_routes_local_coordinates_to_b2(self):
        bucket, key = self.upload()
        with patch.dict(os.environ, {"RTM_ENABLE_LOCAL_DOCUMENT_STORAGE": "0", "B2_BUCKET": bucket}):
            self.assertFalse(storage.local_document_storage_enabled())
            with self.assertRaises(HTTPException) as raised:
                b2_storage.require_http_document_storage()
            self.assertEqual(raised.exception.status_code, 503)
            with self.assertRaises(ValueError):
                b2_storage.validate_b2_object_coordinate(bucket, key)

    def test_configuration_rejects_flag_environment_capability_and_root_errors(self):
        mutations = [
            ("RTM_ENABLE_LOCAL_DOCUMENT_STORAGE", "maybe"),
            ("RTM_ENABLE_LOCAL_OPERATOR_AUTH", "0"),
            ("RTM_ENV", "staging"),
            ("RTM_ENABLE_B2", "1"),
            ("RTM_LOCAL_DOCUMENT_ROOT", "relative/path"),
            ("RTM_LOCAL_DOCUMENT_ROOT", str(storage._CODE_ROOT)),
            ("RTM_LOCAL_DOCUMENT_ROOT", str(storage._CODE_ROOT / "documents")),
            ("RTM_LOCAL_DOCUMENT_ROOT", str(storage._CODE_ROOT.parent)),
            ("RTM_LOCAL_DOCUMENT_ROOT", str(self.root / "missing")),
            ("RTM_LOCAL_DOCUMENT_ROOT", str(self.root / ".." / "documents")),
            ("RTM_LOCAL_DOCUMENT_ROOT", "//server/share/documents"),
        ]
        for name, value in mutations:
            with self.subTest(name=name, value=value), patch.dict(os.environ, {name: value}):
                with self.assertRaises((storage.LocalDocumentStorageMisconfigured, LocalOperatorAuthMisconfigured)):
                    storage.assert_local_document_storage_ready()
                with self.assertRaises(HTTPException) as raised:
                    b2_storage.require_http_document_storage()
                self.assertEqual(raised.exception.status_code, 503)
        with self.assertRaises(storage.LocalDocumentStorageMisconfigured):
            storage.assert_local_document_storage_ready(self.environ | {"RTM_LOCAL_DOCUMENT_ROOT": "bad\x00path"})

    def test_requesting_local_cannot_construct_s3_even_with_b2_enabled(self):
        with patch.dict(os.environ, {"RTM_ENABLE_B2": "1"}), patch.object(b2_storage.boto3, "client") as client:
            with self.assertRaises(LocalOperatorAuthMisconfigured):
                b2_storage.get_s3_client()
            client.assert_not_called()

    def test_coordinates_reject_other_case_provider_traversal_and_platform_aliases(self):
        bucket, key = self.upload()
        keys = [
            key.replace(CASE, OTHER_CASE), key.replace("/original/", "/../"),
            key.replace("cases/", "/cases/"), key.replace("/", "\\"),
            key + ":stream", key + ".", key + " ", " " + key,
            key.replace("/original/", "/original//"),
            key.replace("/original/", "/original/nested/"),
            key.replace(CASE, "not-a-uuid"), key.replace("/original/", "/%2e%2e/"),
            key.replace(".pdf", ".pdf\x00"),
        ]
        for invalid in keys:
            with self.subTest(key=invalid), self.assertRaises(ValueError):
                b2_storage.download_bytes(bucket, invalid, case_id=CASE)
        with self.assertRaises(ValueError):
            b2_storage.download_bytes("external-bucket", key)

    def test_upload_rejects_invalid_names_and_payloads_without_creating_files(self):
        for case, folder, extension in [("bad", "original", ".pdf"), (CASE, "../escape", ".pdf"), (CASE, "original", ".pdf/evil"), (CASE, "CON", ".pdf")]:
            with self.subTest(case=case, folder=folder, extension=extension), self.assertRaises(ValueError):
                storage.upload_bytes(case, folder, b"bytes", extension, "")
        for content in (b"", "text", bytearray(b"data")):
            with self.subTest(content=content), self.assertRaises(ValueError):
                self.upload(content)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_limits_apply_to_full_download_bounded_download_and_upload(self):
        bucket, key = self.upload(b"12345678")
        with self.assertRaises(b2_storage.B2ObjectTooLargeError):
            b2_storage.download_bytes_limited(bucket, key, max_bytes=7)
        for maximum in (0, -1, True, "8", storage.MAX_LOCAL_DOCUMENT_BYTES + 1):
            with self.subTest(maximum=maximum), self.assertRaises(ValueError):
                b2_storage.download_bytes_limited(bucket, key, max_bytes=maximum)
        with patch.object(storage, "MAX_LOCAL_DOCUMENT_BYTES", 4):
            with self.assertRaises(b2_storage.B2ObjectTooLargeError):
                self.upload(b"12345")
            with self.assertRaises(b2_storage.B2ObjectTooLargeError):
                b2_storage.download_bytes(bucket, key)

    def test_hash_size_and_truncation_are_verified_before_returning_payload(self):
        bucket, key = self.upload(b"12345678")
        path = self.root / key
        original = path.read_bytes()
        for corrupted in (original[:-1], original + b"x", original[:-1] + b"x", b"bad", b"BADMAGIC" + original[8:]):
            with self.subTest(length=len(corrupted)):
                path.write_bytes(corrupted)
                with self.assertRaises(storage.LocalDocumentIntegrityError):
                    b2_storage.download_bytes(bucket, key)
        path.write_bytes(original)
        self.assertEqual(b2_storage.download_bytes(bucket, key), b"12345678")

    def test_compensation_removes_only_exact_coordinate_and_is_idempotent(self):
        first = self.upload(b"first")
        second = self.upload(b"second")
        b2_storage.delete_object(*first)
        b2_storage.delete_object(*first)
        self.assertFalse((self.root / first[1]).exists())
        self.assertEqual(b2_storage.download_bytes(*second), b"second")
        with self.assertRaises(ValueError):
            b2_storage.delete_object(first[0], first[1].rsplit("/", 1)[0])

    def test_publication_failure_leaves_no_object_or_temporary_file(self):
        with patch.object(storage.os, "fsync", side_effect=OSError("synthetic disk failure")):
            with self.assertRaises(OSError):
                self.upload()
        self.assertEqual([p for p in self.root.rglob("*") if p.is_file()], [])

    def test_generated_collision_never_overwrites_existing_object(self):
        identifier = uuid.UUID("11111111-1111-4111-8111-111111111111")
        with patch.object(storage.uuid, "uuid4", return_value=identifier):
            coordinate = self.upload(b"first")
            with self.assertRaises(FileExistsError):
                self.upload(b"second")
        self.assertEqual(b2_storage.download_bytes(*coordinate), b"first")
        self.assertEqual(len([p for p in self.root.rglob("*") if p.is_file()]), 1)

    def test_symlinked_root_and_descendants_are_rejected_without_touching_target(self):
        outside = Path(self.temporary.name) / "outside"
        outside.mkdir()
        root_link = Path(self.temporary.name) / "root-link"
        try:
            root_link.symlink_to(self.root, target_is_directory=True)
        except OSError:
            self.skipTest("This platform does not grant symlink creation")
        with patch.dict(os.environ, {"RTM_LOCAL_DOCUMENT_ROOT": str(root_link)}):
            with self.assertRaises(storage.LocalDocumentStorageMisconfigured):
                self.upload()
        (self.root / "cases").symlink_to(outside, target_is_directory=True)
        with self.assertRaises((OSError, ValueError)):
            self.upload()
        self.assertEqual(list(outside.iterdir()), [])

    def test_file_symlinks_and_hardlinks_are_never_read_or_deleted(self):
        bucket, key = self.upload(b"inside")
        path = self.root / key
        outside = Path(self.temporary.name) / "outside-file"
        outside.write_bytes(path.read_bytes())
        path.unlink()
        try:
            path.symlink_to(outside)
        except OSError:
            self.skipTest("This platform does not grant symlink creation")
        for action in (b2_storage.download_bytes, b2_storage.delete_object):
            with self.assertRaises(ValueError):
                action(bucket, key)
        path.unlink()
        os.link(outside, path)
        for action in (b2_storage.download_bytes, b2_storage.delete_object):
            with self.assertRaises(ValueError):
                action(bucket, key)
        self.assertTrue(outside.exists())

    def test_local_provider_never_produces_a_presigned_url(self):
        bucket, key = self.upload()
        with patch.object(b2_storage.boto3, "client") as client:
            with self.assertRaisesRegex(RuntimeError, "descarga autenticada"):
                b2_storage.presign_get_url(bucket, key)
            client.assert_not_called()

    def test_windows_directory_protocol_leaves_volume_anchor_unlocked_and_closes_handles(self):
        # Exercise API arguments/cleanup without claiming a native Windows run.
        kernel = Mock()
        kernel.CreateFileW.side_effect = [123, 456]
        paths = [(Path(self.root.anchor), False), (self.root.parent, False), (self.root, False)]
        with patch.object(ctypes, "WinDLL", return_value=kernel, create=True):
            with storage._windows_directories(paths):
                self.assertEqual(kernel.CreateFileW.call_count, 2)
                for call in kernel.CreateFileW.call_args_list:
                    self.assertEqual(call.args[1:6], (0x80, 1, None, 3, 0x02200000))
                kernel.CloseHandle.assert_not_called()
        self.assertEqual([call.args[0] for call in kernel.CloseHandle.call_args_list], [456, 123])

    def test_windows_sharing_conflict_fails_closed_and_closes_previous_handles(self):
        kernel = Mock()
        kernel.CreateFileW.side_effect = [123, ctypes.c_void_p(-1).value]
        paths = [(self.root.parent, False), (self.root, False)]
        with patch.object(ctypes, "WinDLL", return_value=kernel, create=True), patch.object(ctypes, "get_last_error", return_value=32, create=True):
            with self.assertRaises(OSError):
                with storage._windows_directories(paths):
                    self.fail("A sharing conflict cannot enter the storage operation")
        kernel.CloseHandle.assert_called_once_with(123)


if __name__ == "__main__":
    unittest.main()

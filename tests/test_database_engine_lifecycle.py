from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock, patch

import database


class DatabaseEngineLifecycleTest(unittest.TestCase):
    def setUp(self):
        database.dispose_all_engines()

    def tearDown(self):
        database.dispose_all_engines()

    def test_same_url_reuses_one_engine_and_pool(self):
        engine = MagicMock(name="engine")
        url = "postgresql+psycopg://user:pass@db.example/rtm"

        with patch.dict(os.environ, {"DATABASE_URL": url}, clear=False):
            with patch("database.create_engine", return_value=engine) as create:
                first = database.get_engine()
                second = database.get_engine()

        self.assertIs(first, engine)
        self.assertIs(second, engine)
        create.assert_called_once_with(
            url,
            pool_pre_ping=True,
            hide_parameters=True,
        )

    def test_different_urls_never_share_engine(self):
        first_engine = MagicMock(name="first_engine")
        second_engine = MagicMock(name="second_engine")
        first_url = "postgresql+psycopg://user:pass@db.example/rtm_a"
        second_url = "postgresql+psycopg://user:pass@db.example/rtm_b"

        with patch(
            "database.create_engine",
            side_effect=[first_engine, second_engine],
        ) as create:
            with patch.dict(os.environ, {"DATABASE_URL": first_url}, clear=False):
                first = database.get_engine()
            with patch.dict(os.environ, {"DATABASE_URL": second_url}, clear=False):
                second = database.get_engine()

        self.assertIs(first, first_engine)
        self.assertIs(second, second_engine)
        self.assertIsNot(first, second)
        self.assertEqual(create.call_count, 2)

    def test_dispose_closes_pools_and_allows_clean_recreation(self):
        old_engine = MagicMock(name="old_engine")
        new_engine = MagicMock(name="new_engine")
        url = "postgresql+psycopg://user:pass@db.example/rtm"

        with patch.dict(os.environ, {"DATABASE_URL": url}, clear=False):
            with patch(
                "database.create_engine",
                side_effect=[old_engine, new_engine],
            ):
                self.assertIs(database.get_engine(), old_engine)
                self.assertEqual(database.dispose_all_engines(), 1)
                self.assertIs(database.get_engine(), new_engine)

        old_engine.dispose.assert_called_once_with()
        new_engine.dispose.assert_not_called()

    def test_missing_database_url_fails_closed(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "DATABASE_URL"):
                database.get_engine()


if __name__ == "__main__":
    unittest.main()

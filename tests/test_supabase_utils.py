"""Tests for core.supabase_utils shared client and upsert helpers."""

import os
from unittest import mock

from core.supabase_utils import (
    get_public_supabase_client,
    get_service_role_client,
    read_public_supabase_creds,
    upsert_rows,
)

import pytest


class TestReadPublicSupabaseCreds:
    def test_returns_none_when_env_empty(self) -> None:
        with mock.patch.dict(os.environ, {"SUPABASE_URL": "", "SUPABASE_ANON_KEY": ""}, clear=False):
            url, key = read_public_supabase_creds()
        assert url is None
        assert key is None

    def test_reads_from_env(self) -> None:
        with mock.patch.dict(
            os.environ,
            {"SUPABASE_URL": "https://test.supabase.co", "SUPABASE_ANON_KEY": "anon-key-123"},
            clear=False,
        ):
            url, key = read_public_supabase_creds()
        assert url == "https://test.supabase.co"
        assert key == "anon-key-123"


class TestGetServiceRoleClient:
    def test_raises_when_env_missing(self) -> None:
        with mock.patch.dict(os.environ, {"SUPABASE_URL": "", "SUPABASE_SERVICE_ROLE_KEY": ""}, clear=False):
            with pytest.raises(RuntimeError, match="missing"):
                get_service_role_client()


class TestGetPublicSupabaseClient:
    def test_returns_none_when_no_creds(self) -> None:
        with mock.patch.dict(os.environ, {"SUPABASE_URL": "", "SUPABASE_ANON_KEY": ""}, clear=False):
            client = get_public_supabase_client()
        assert client is None


class TestUpsertRows:
    def test_returns_zero_for_empty_rows(self) -> None:
        result = upsert_rows(mock.MagicMock(), "public", "games", [])
        assert result == 0

    def test_calls_upsert_with_on_conflict(self) -> None:
        client = mock.MagicMock()
        mock_resp = mock.MagicMock()
        mock_resp.data = [{"id": "1"}, {"id": "2"}]
        client.schema.return_value.table.return_value.upsert.return_value.execute.return_value = mock_resp

        result = upsert_rows(client, "public", "games", [{"id": "1"}, {"id": "2"}], on_conflict="game_id")

        assert result == 2
        client.schema.assert_called_once_with("public")
        client.schema.return_value.table.assert_called_once_with("games")

    def test_single_row_passed_as_dict(self) -> None:
        client = mock.MagicMock()
        mock_resp = mock.MagicMock()
        mock_resp.data = [{"id": "1"}]
        client.schema.return_value.table.return_value.upsert.return_value.execute.return_value = mock_resp

        result = upsert_rows(client, "raw", "raw_games", [{"id": "1"}])

        assert result == 1
        # Single row should be unwrapped from list
        client.schema.return_value.table.return_value.upsert.assert_called_once_with({"id": "1"})

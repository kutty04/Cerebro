import os
import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from app import app

client = TestClient(app)

TEST_SECRET = "test_keepalive_secret_12345"

class TestKeepaliveEndpoint:
    def test_keepalive_no_header_returns_401(self):
        with patch.dict(os.environ, {"KEEPALIVE_SECRET": TEST_SECRET}):
            response = client.get("/keepalive")
            assert response.status_code == 401
            assert response.json() == {"detail": "Unauthorized"}

    def test_keepalive_wrong_header_returns_401(self):
        with patch.dict(os.environ, {"KEEPALIVE_SECRET": TEST_SECRET}):
            response = client.get("/keepalive", headers={"X-Keepalive-Key": "wrong_secret"})
            assert response.status_code == 401
            assert response.json() == {"detail": "Unauthorized"}

    def test_keepalive_missing_env_secret_returns_401(self):
        with patch.dict(os.environ, {"KEEPALIVE_SECRET": ""}):
            response = client.get("/keepalive", headers={"X-Keepalive-Key": TEST_SECRET})
            assert response.status_code == 401
            assert response.json() == {"detail": "Unauthorized"}

    @patch("app.db")
    def test_keepalive_valid_header_and_db_read_returns_200(self, mock_db):
        mock_table = MagicMock()
        mock_select = MagicMock()
        mock_limit = MagicMock()
        mock_execute = MagicMock(return_value=MagicMock(data=[{"id": "some_repo_id"}]))

        mock_db.table.return_value = mock_table
        mock_table.select.return_value = mock_select
        mock_select.limit.return_value = mock_limit
        mock_limit.execute = mock_execute

        with patch.dict(os.environ, {"KEEPALIVE_SECRET": TEST_SECRET}):
            response = client.get("/keepalive", headers={"X-Keepalive-Key": TEST_SECRET})
            assert response.status_code == 200
            assert response.json() == {"status": "ok", "database": "reachable"}

            # Verify exact read-only query
            mock_db.table.assert_called_once_with("user_repositories")
            mock_table.select.assert_called_once_with("id")
            mock_select.limit.assert_called_once_with(1)
            mock_limit.execute.assert_called_once()

            # Assert no insert, update, or delete methods were called
            assert not mock_table.insert.called
            assert not mock_table.update.called
            assert not mock_table.delete.called

    @patch("app.db")
    def test_keepalive_valid_header_and_db_failure_returns_503(self, mock_db):
        mock_table = MagicMock()
        mock_select = MagicMock()
        mock_limit = MagicMock()
        mock_limit.execute.side_effect = Exception("Database connection timeout")

        mock_db.table.return_value = mock_table
        mock_table.select.return_value = mock_select
        mock_select.limit.return_value = mock_limit

        with patch.dict(os.environ, {"KEEPALIVE_SECRET": TEST_SECRET}):
            response = client.get("/keepalive", headers={"X-Keepalive-Key": TEST_SECRET})
            assert response.status_code == 503
            assert response.json() == {"detail": "Database unavailable"}

            # Assert no insert, update, or delete methods were called
            assert not mock_table.insert.called
            assert not mock_table.update.called
            assert not mock_table.delete.called

import pytest
from unittest.mock import patch, MagicMock
from telemetry import get_cache_key, get_cached_query, set_cached_query

USER_A = "usr_aaaa_1111"
USER_B = "usr_bbbb_2222"

QUERY = "What is the entrypoint of this project?"

class TestCacheTenantIsolation:

    def test_cache_keys_are_unique_per_user(self):
        key_a = get_cache_key(query=QUERY, user_id=USER_A, repo_scope="ALL", index_version="v1")
        key_b = get_cache_key(query=QUERY, user_id=USER_B, repo_scope="ALL", index_version="v1")

        assert key_a != key_b
        assert len(key_a) == 64  # SHA-256 hex string

    def test_cache_keys_are_unique_per_repository_scope(self):
        key_jarvis = get_cache_key(query=QUERY, user_id=USER_A, repo_scope="Jarvis-portfolio", index_version="v1")
        key_bus = get_cache_key(query=QUERY, user_id=USER_A, repo_scope="bus-crowding", index_version="v1")
        key_all = get_cache_key(query=QUERY, user_id=USER_A, repo_scope="ALL", index_version="v1")

        assert len({key_jarvis, key_bus, key_all}) == 3

    def test_cache_keys_are_unique_per_index_version(self):
        key_v1 = get_cache_key(query=QUERY, user_id=USER_A, repo_scope="Jarvis-portfolio", index_version="v1")
        key_v2 = get_cache_key(query=QUERY, user_id=USER_A, repo_scope="Jarvis-portfolio", index_version="v2")

        assert key_v1 != key_v2

    def test_cache_hit_and_miss_isolation(self):
        sources_a = [{"repo": "Jarvis-portfolio", "file": "main.py", "code": "def main(): pass"}]
        sources_b = [{"repo": "bus-crowding", "file": "app.py", "code": "def run(): pass"}]

        # Set cache for User A
        set_cached_query(
            query=QUERY,
            user_id=USER_A,
            repo_scope="Jarvis-portfolio",
            answer="User A's answer for Jarvis",
            sources=sources_a,
            confidence=95,
            index_version="v1"
        )

        # 1. User A gets cache hit
        cached_a = get_cached_query(query=QUERY, user_id=USER_A, repo_scope="Jarvis-portfolio", index_version="v1")
        assert cached_a is not None
        assert cached_a["answer"] == "User A's answer for Jarvis"
        assert cached_a["sources"] == sources_a

        # 2. User B gets cache miss for same query & repo
        cached_b = get_cached_query(query=QUERY, user_id=USER_B, repo_scope="Jarvis-portfolio", index_version="v1")
        assert cached_b is None

        # 3. User A gets cache miss for different repo
        cached_a_bus = get_cached_query(query=QUERY, user_id=USER_A, repo_scope="bus-crowding", index_version="v1")
        assert cached_a_bus is None

        # 4. User A gets cache miss after re-index (v1 -> v2)
        cached_a_v2 = get_cached_query(query=QUERY, user_id=USER_A, repo_scope="Jarvis-portfolio", index_version="v2")
        assert cached_a_v2 is None

    def test_old_cache_key_format_safely_misses(self):
        # Unqualified query without user_id must return None
        assert get_cached_query(query=QUERY, user_id=None, repo_scope="ALL", index_version="v1") is None
        assert get_cached_query(query=QUERY, user_id="", repo_scope="ALL", index_version="v1") is None

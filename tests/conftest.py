"""
Shared pytest fixtures for the Cerebro backend test suite.

The only transport-level concern addressed here is ensuring that the
connection-pooled HTTP clients (app.http_client, indexer.http_client) never
make real network calls during tests.  We patch the use-site attributes
directly rather than patching requests.post, because Session.post() is
independent of the module-level requests.post callable.

All per-test HF response shapes are configured inside individual tests by
re-patching app.http_client.post / indexer.http_client.post with a more
specific MagicMock.  This fixture only provides the safe default (no real
network) and a reusable HF response factory.
"""

import json
import os
import pytest
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# HF response factory
# ---------------------------------------------------------------------------

def make_hf_llm_response(
    answer: str = "Test answer",
    summary: str = "Short summary",
    citation_ids: list | None = None,
    follow_ups: list | None = None,
    limitations: list | None = None,
    status_code: int = 200,
) -> MagicMock:
    """
    Returns a MagicMock that mimics a successful Hugging Face LLM response.
    Reuse in individual tests to keep mock shapes consistent.
    """
    mock_resp = MagicMock()
    mock_resp.status_code = status_code
    mock_resp.json.return_value = {
        "choices": [
            {
                "message": {
                    "content": json.dumps(
                        {
                            "answer": answer,
                            "summary": summary,
                            "citation_ids": citation_ids if citation_ids is not None else [],
                            "follow_ups": follow_ups if follow_ups is not None else [],
                            "limitations": limitations if limitations is not None else [],
                        }
                    )
                }
            }
        ]
    }
    return mock_resp


def make_hf_embedding_response(
    embeddings: list[list[float]] | None = None,
    status_code: int = 200,
) -> MagicMock:
    """
    Returns a MagicMock that mimics a successful Hugging Face embedding response.
    """
    mock_resp = MagicMock()
    mock_resp.status_code = status_code
    mock_resp.json.return_value = embeddings if embeddings is not None else [[0.1] * 384]
    return mock_resp


# ---------------------------------------------------------------------------
# Network-isolation autouse fixture
#
# Patches the use-site attributes on both HTTP clients so that any test that
# forgets to provide its own mock still fails fast (RuntimeError) rather than
# making a real network call.
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def block_real_http(request):
    """
    Prevents real HTTP calls through app.http_client or indexer.http_client
    by replacing .post with a sentinel that raises RuntimeError.

    Individual tests that need a specific response should patch the same
    attributes inside a `with patch("app.http_client.post", ...)` block;
    that inner patch takes precedence over this fixture.

    The fixture is skipped for tests marked with @pytest.mark.allow_real_http.
    """
    if "allow_real_http" in request.keywords:
        yield
        return

    def _no_real_post(*args, **kwargs):
        raise RuntimeError(
            "Test attempted a real HTTP POST through http_client.  "
            "Patch app.http_client.post or indexer.http_client.post in your test."
        )

    with patch("app.http_client.post", side_effect=_no_real_post), \
         patch("indexer.http_client.post", side_effect=_no_real_post):
        yield

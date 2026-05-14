"""Regression test for the Supabase list_customers URL encoding bug.

When the frontend's Customer Manager search box was used, the agent
built a Supabase ilike URL with literal '%' chars around the search
term, e.g. `ilike.%HL%`. PostgREST treated '%H' as a malformed percent
escape and returned 500.

The fix encodes the '%' wildcards as '%25' so PostgREST receives a
literal '%' after URL decoding.
"""
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _make_response(status_code=200, json_data=None):
    resp = MagicMock()
    resp.status_code = status_code
    resp.is_success = 200 <= status_code < 300
    resp.json = MagicMock(return_value=json_data or [])
    resp.raise_for_status = MagicMock()
    resp.text = ""
    return resp


def test_search_pattern_percent_chars_are_url_encoded():
    """The bug: ilike.%HLLOGI01% was sent literally and Supabase 500'd.
    The fix: literal '%' wildcards must be encoded as '%25' in the URL."""
    with patch.dict("os.environ", {"SUPABASE_URL": "https://test.supabase.co", "SUPABASE_SERVICE_KEY": "key"}):
        with patch("httpx.get") as mock_get:
            mock_get.return_value = _make_response(json_data=[])
            # Import inside the patch so the module picks up our env vars
            import importlib
            import services.supabase_client as sb
            importlib.reload(sb)

            sb.list_customers(search="HLLOGI01", active_only=True)

            assert mock_get.called, "httpx.get was not called"
            called_url = mock_get.call_args[0][0]

            # Critical: the % wildcards must be encoded as %25
            assert "%25HLLOGI01%25" in called_url, \
                f"Expected `%25HLLOGI01%25` in URL (encoded `%HLLOGI01%`), got:\n  {called_url}"

            # Negative: must NOT contain bare %H or %, which is what triggered the bug
            assert "ilike.%HLLOGI01%" not in called_url, \
                f"Regression — URL still contains the unencoded pattern:\n  {called_url}"


def test_search_with_special_chars_does_not_break_url():
    """Search terms with spaces / special chars should be URL-quoted."""
    with patch.dict("os.environ", {"SUPABASE_URL": "https://test.supabase.co", "SUPABASE_SERVICE_KEY": "key"}):
        with patch("httpx.get") as mock_get:
            mock_get.return_value = _make_response(json_data=[])
            import importlib
            import services.supabase_client as sb
            importlib.reload(sb)

            sb.list_customers(search="HL Logistics & Co", active_only=True)

            called_url = mock_get.call_args[0][0]
            # Space → %20, & → %26 (quoted, safe="")
            assert "%20" in called_url or "+" in called_url, \
                f"Space not encoded in URL: {called_url}"


def test_empty_search_omits_or_clause():
    with patch.dict("os.environ", {"SUPABASE_URL": "https://test.supabase.co", "SUPABASE_SERVICE_KEY": "key"}):
        with patch("httpx.get") as mock_get:
            mock_get.return_value = _make_response(json_data=[])
            import importlib
            import services.supabase_client as sb
            importlib.reload(sb)

            sb.list_customers(search="", active_only=True)

            called_url = mock_get.call_args[0][0]
            assert "or=" not in called_url, f"Empty search should not produce `or=` clause: {called_url}"
            assert "active=eq.true" in called_url, f"active filter missing: {called_url}"


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))

"""Browser URL helpers for the daemon's path-scoped WebSocket endpoint."""

from __future__ import annotations

from murder.app.cli.web_cmd import _http_base_from_websocket_url


def test_http_base_from_path_scoped_websocket_url() -> None:
    assert (
        _http_base_from_websocket_url("ws://127.0.0.1:62077/api/ws/repo-abc")
        == "http://127.0.0.1:62077/"
    )


def test_http_base_from_legacy_flat_websocket_url() -> None:
    assert (
        _http_base_from_websocket_url("ws://127.0.0.1:9001/api/ws")
        == "http://127.0.0.1:9001/"
    )


def test_http_base_maps_wss_to_https() -> None:
    assert (
        _http_base_from_websocket_url("wss://murder.example/api/ws/r1")
        == "https://murder.example/"
    )

"""Browser URL helpers for the fixed daemon origin."""

from __future__ import annotations

from murder.app.cli.web_cmd import daemon_browser_url
from murder.app.protocol.common import DAEMON_WEBSOCKET_HOST, DAEMON_WEBSOCKET_PORT


def test_daemon_browser_url_is_fixed_origin() -> None:
    assert daemon_browser_url() == f"http://{DAEMON_WEBSOCKET_HOST}:{DAEMON_WEBSOCKET_PORT}/"

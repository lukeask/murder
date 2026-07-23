"""``image.*`` RPC handlers."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from murder.app.protocol.requests import CommandName

if TYPE_CHECKING:
    from murder.app.service.host import ServiceHost


def register(host: ServiceHost) -> None:
    def _image_upload(body: dict[str, Any]) -> dict[str, Any]:
        # F9: store a pasted clipboard image under .murder/images and return
        # the stored path. Bytes ride base64 over JSON-RPC.
        #
        # The client now mints the filename ``stem`` at paste time and passes
        # it as ``name`` (so the label<->file binding is known instantly,
        # client-side). The server no longer mints it. But the service NEVER
        # trusts a path from the wire: both ``name`` and ``ext`` are
        # sanitized to the basename charset before being joined into the
        # path, so a traversal attempt (``../../etc/foo``) collapses to a
        # harmless basename. This guard is unconditional (the bus is a local
        # The application WebSocket is client-authenticated, but the invariant holds
        # regardless).
        import base64
        import re

        from murder.state.storage.paths import murder_dir as _murder_dir

        data_b64 = body.get("bytes")
        if not isinstance(data_b64, str) or not data_b64:
            raise ValueError("image.upload requires base64 bytes")
        # Cap the base64 payload before decoding so a malicious/oversized
        # upload can't be expanded to disk. The TCP listener (optional, see
        # ``start_tcp_listener``) makes this reachable from an
        # unauthenticated client, so the bound is unconditional. 32 MiB
        # decoded is generous for a pasted clipboard image.
        _MAX_IMAGE_BYTES = 32 * 1024 * 1024
        # base64 is 4/3 the size of the decoded bytes; reject early.
        if len(data_b64) > (_MAX_IMAGE_BYTES * 4) // 3 + 16:
            return {"ok": False, "error": "image too large"}
        try:
            data = base64.b64decode(data_b64, validate=True)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"invalid base64: {exc}"}
        if len(data) > _MAX_IMAGE_BYTES:
            return {"ok": False, "error": "image too large"}

        def _sanitize(value: str) -> str:
            return re.sub(r"[^a-zA-Z0-9._-]", "", value)

        stem = _sanitize(str(body.get("name") or ""))
        if not stem:
            return {"ok": False, "error": "image.upload requires a non-empty name"}
        ext = _sanitize(str(body.get("ext") or "png").lstrip(".")) or "png"

        images_dir = _murder_dir(host.repo_root) / "images"
        images_dir.mkdir(parents=True, exist_ok=True)
        fpath = images_dir / f"{stem}.{ext}"
        fpath.write_bytes(data)
        return {"ok": True, "path": str(fpath)}

    host.register_application_command(CommandName.IMAGE_UPLOAD, _image_upload)

"""``image.*`` application handlers."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from murder.app.protocol.lifecycle import ImageUploadParams, ImageUploadResult
from murder.app.protocol.requests import CommandName

if TYPE_CHECKING:
    from murder.app.service.host import ServiceHost


def register(host: ServiceHost) -> None:
    def _image_upload(body: dict[str, Any]) -> dict[str, Any]:
        # F9: store a pasted clipboard image under .murder/images and return
        # the stored path. Bytes ride base64 over the application protocol.
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

        params = ImageUploadParams.model_validate(body)
        data_b64 = params.bytes
        # Cap the base64 payload before decoding so a malicious/oversized
        # upload can't be expanded to disk. The TCP listener (optional, see
        # ``start_tcp_listener``) makes this reachable from an
        # unauthenticated client, so the bound is unconditional. 32 MiB
        # decoded is generous for a pasted clipboard image.
        _MAX_IMAGE_BYTES = 32 * 1024 * 1024
        # base64 is 4/3 the size of the decoded bytes; reject early.
        if len(data_b64) > (_MAX_IMAGE_BYTES * 4) // 3 + 16:
            return ImageUploadResult(
                ok=False, error="image too large", error_code="image_too_large"
            ).model_dump(mode="json")
        try:
            data = base64.b64decode(data_b64, validate=True)
        except Exception as exc:  # noqa: BLE001
            return ImageUploadResult(
                ok=False,
                error=f"invalid base64: {exc}",
                error_code="invalid_base64",
            ).model_dump(mode="json")
        if len(data) > _MAX_IMAGE_BYTES:
            return ImageUploadResult(
                ok=False, error="image too large", error_code="image_too_large"
            ).model_dump(mode="json")

        def _sanitize(value: str) -> str:
            return re.sub(r"[^a-zA-Z0-9._-]", "", value)

        stem = _sanitize(params.name)
        if not stem:
            return ImageUploadResult(
                ok=False,
                error="image.upload requires a non-empty name",
                error_code="empty_name",
            ).model_dump(mode="json")
        ext = _sanitize((params.ext or "png").lstrip(".")) or "png"

        images_dir = _murder_dir(host.repo_root) / "images"
        images_dir.mkdir(parents=True, exist_ok=True)
        fpath = images_dir / f"{stem}.{ext}"
        fpath.write_bytes(data)
        return ImageUploadResult(ok=True, path=str(fpath)).model_dump(mode="json")

    host.register_application_command(CommandName.IMAGE_UPLOAD, _image_upload)

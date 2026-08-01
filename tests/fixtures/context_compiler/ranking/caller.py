"""Direct caller of ProfileEditor.save — expansion via ``calls`` should reach here."""

from editor import ProfileEditor


def invoke_save(payload: dict | None = None) -> dict:
    return ProfileEditor().save(payload or {})

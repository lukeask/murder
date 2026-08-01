"""Edit-target fixture for Step 4 ranking / profile comparison cases."""

from contracts import ProfileContract


class ProfileEditor:
    """Owns profile save behavior."""

    def __init__(self, contract: ProfileContract | None = None) -> None:
        self.contract = contract or ProfileContract()

    def save(self, payload: dict) -> dict:
        validated = self.contract.validate(payload)
        return {"saved": validated}

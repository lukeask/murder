"""Public contract consumed by ProfileEditor — planning should reach this."""


class ProfileContract:
    def validate(self, payload: dict) -> dict:
        if "name" not in payload:
            raise ValueError("name required")
        return payload


def public_save_api(payload: dict) -> dict:
    return ProfileContract().validate(payload)

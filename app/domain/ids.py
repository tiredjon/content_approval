import secrets


def generate_id(prefix: str) -> str:
    """Opaque, unguessable entity id, e.g. generate_id("ar") -> "ar_3f9a2b1c..."."""
    return f"{prefix}_{secrets.token_hex(12)}"

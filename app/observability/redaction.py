from typing import Any

REDACTED = "[REDACTED]"

# Substring match, case-insensitive — deliberately broad so e.g. "signedUrl",
# "storage_key", "providerUrl" all get caught alongside the literal constraint wording
# (secrets, tokens, emails, storage keys, signed URLs, provider URLs). Belt-and-suspenders:
# this service never handles such data directly (only opaque external ids), but log
# lines and error bodies are built defensively in case a future field slips in.
_SENSITIVE_MARKERS = (
    "password",
    "secret",
    "token",
    "authorization",
    "api_key",
    "apikey",
    "email",
    "storage_key",
    "storagekey",
    "signed_url",
    "signedurl",
    "provider_url",
    "providerurl",
    "credential",
)


def looks_sensitive(key: str) -> bool:
    lowered = key.lower()
    return any(marker in lowered for marker in _SENSITIVE_MARKERS)


def redact_mapping(mapping: dict[str, Any]) -> dict[str, Any]:
    return {k: (REDACTED if looks_sensitive(k) else v) for k, v in mapping.items()}

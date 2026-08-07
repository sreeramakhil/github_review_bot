"""
Verifies GitHub webhook payloads using the X-Hub-Signature-256 header.
Never process a webhook body without verifying this first — otherwise
anyone who finds your endpoint URL can trigger fake reviews / spam
your Ollama instance / spoof PR content.
"""
import hashlib
import hmac


def verify_signature(payload_body: bytes, secret: str, signature_header: str | None) -> bool:
    if not secret:
        # Explicitly misconfigured — fail closed, not open.
        return False
    if not signature_header or not signature_header.startswith("sha256="):
        return False

    expected = hmac.new(secret.encode("utf-8"), payload_body, hashlib.sha256).hexdigest()
    provided = signature_header.split("=", 1)[1]

    # Constant-time comparison to avoid timing attacks.
    return hmac.compare_digest(expected, provided)

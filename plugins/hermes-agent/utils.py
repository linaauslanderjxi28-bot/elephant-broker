from __future__ import annotations

import hashlib
import uuid


def stable_uuid(text: str) -> str:
    if not text:
        return str(uuid.UUID(int=0))
    try:
        return str(uuid.UUID(text))
    except (ValueError, TypeError):
        pass
    return str(uuid.UUID(hashlib.sha256(str(text).encode("utf-8")).hexdigest()[:32]))

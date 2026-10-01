"""Answer cache for paid AI providers (Claude): ask the same question twice,
pay once. Every failure here is swallowed - a broken cache must never break a
report; it just behaves as a miss."""

import hashlib
import logging

from sqlalchemy import func

from app.config import settings

logger = logging.getLogger(__name__)


def enabled() -> bool:
    return bool(getattr(settings, "ai_response_cache_enabled", True))


def make_key(provider: str, model: str | None, effort: str | None, module: str, extra: str, prompt: str) -> str:
    h = hashlib.sha256()
    for part in (provider, model or "", effort or "", module, extra):
        h.update(part.encode("utf-8", "replace"))
        h.update(b"\x1f")
    h.update(prompt.encode("utf-8", "replace"))
    return h.hexdigest()


def get(key: str) -> dict | None:
    """{"response", "input_tokens", "output_tokens"} for a hit (and counts it), else None."""
    try:
        from app.db.session import SessionLocal
        from app.models.ai_response_cache import AiResponseCache

        db = SessionLocal()
        try:
            row = db.get(AiResponseCache, key)
            if row is None:
                return None
            row.hits = (row.hits or 0) + 1
            row.last_used_at = func.now()
            out = {"response": row.response, "input_tokens": row.input_tokens, "output_tokens": row.output_tokens}
            db.commit()
            return out
        finally:
            db.close()
    except Exception as e:  # noqa: BLE001
        logger.warning("AI cache lookup failed (treated as a miss): %s", e)
        return None


def put(key: str, module: str, provider: str, model: str | None, response: str, input_tokens: int, output_tokens: int) -> None:
    try:
        from app.db.session import SessionLocal
        from app.models.ai_response_cache import AiResponseCache

        db = SessionLocal()
        try:
            row = db.get(AiResponseCache, key)
            if row is None:
                db.add(AiResponseCache(
                    cache_key=key, module=module, provider=provider, model=model, response=response,
                    input_tokens=int(input_tokens or 0), output_tokens=int(output_tokens or 0),
                ))
            else:
                row.response = response
                row.input_tokens, row.output_tokens = int(input_tokens or 0), int(output_tokens or 0)
            db.commit()
        finally:
            db.close()
    except Exception as e:  # noqa: BLE001
        logger.warning("AI cache store failed (answer not saved): %s", e)


def delete(key: str) -> None:
    try:
        from app.db.session import SessionLocal
        from app.models.ai_response_cache import AiResponseCache

        db = SessionLocal()
        try:
            db.query(AiResponseCache).filter(AiResponseCache.cache_key == key).delete()
            db.commit()
        finally:
            db.close()
    except Exception as e:  # noqa: BLE001
        logger.warning("AI cache delete failed: %s", e)


def clear_all() -> int:
    from app.db.session import SessionLocal
    from app.models.ai_response_cache import AiResponseCache

    db = SessionLocal()
    try:
        n = db.query(AiResponseCache).delete()
        db.commit()
        return n
    finally:
        db.close()

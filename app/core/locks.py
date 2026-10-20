"""
app/core/locks.py
Trava distribuída simples (Redis SET NX) para impedir coletas concorrentes contra o mesmo portal.

Motivo: o Portal da Transparência limita a taxa por IP. Coletas simultâneas competem pelo mesmo
limite (429 em cascata) e gravavam o mesmo mês duas vezes.
"""

from typing import Optional

import redis

from app.core.config import settings

DF_COLLECT_LOCK = "lock:collect:df"
# Maior que o task_time_limit (24 h): a coleta pode durar horas e a trava não pode expirar no meio
DEFAULT_TTL_SECONDS = 25 * 3600


def _client() -> redis.Redis:
    return redis.Redis.from_url(settings.redis_url, decode_responses=True)


def acquire(key: str, owner: str, ttl: int = DEFAULT_TTL_SECONDS) -> bool:
    """Tenta adquirir a trava. True se conseguiu."""
    return bool(_client().set(key, owner, nx=True, ex=ttl))


def release(key: str, owner: str) -> None:
    """Libera a trava somente se ainda for do `owner` (evita soltar a trava de outro)."""
    client = _client()
    if client.get(key) == owner:
        client.delete(key)


def current_owner(key: str) -> Optional[str]:
    return _client().get(key)


def clear(key: str) -> None:
    _client().delete(key)


# --- Bloqueio global por rate limit (429) ---
# Quando o portal responde 429 com Retry-After, TODAS as requisições (de qualquer worker) devem
# esperar. Insistir durante o bloqueio só o prolonga.
DF_BAN_KEY = "ratelimit:df:until"


def set_ban(seconds: float) -> None:
    """Registra que o portal está bloqueando pelos próximos `seconds` segundos."""
    import time

    until = time.time() + seconds
    client = _client()
    current = client.get(DF_BAN_KEY)
    if not current or float(current) < until:
        client.set(DF_BAN_KEY, str(until), ex=int(seconds) + 60)


def ban_remaining() -> float:
    """Segundos restantes de bloqueio (0 se livre)."""
    import time

    value = _client().get(DF_BAN_KEY)
    return max(0.0, float(value) - time.time()) if value else 0.0

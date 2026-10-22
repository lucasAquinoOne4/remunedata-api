import os
from unittest.mock import AsyncMock

import pytest
from circuitbreaker import CircuitBreakerMonitor
from fastapi_limiter import FastAPILimiter

# Estado global para controle de mocks sem conflitos de loop
GLOBAL_TEST_STATE = {"force_429": False, "429_threshold": 30, "calls": 0}

# Configuração de ambiente para testes
os.environ.setdefault(
    "DATABASE_URL", "postgresql+asyncpg://postgres:postgres@localhost:5432/df_remuneration_test"
)
os.environ.setdefault(
    "DATABASE_URL_SYNC",
    "postgresql+psycopg2://postgres:postgres@localhost:5432/df_remuneration_test",
)
os.environ.setdefault("APP_ENV", "testing")
os.environ.setdefault("LOG_LEVEL", "WARNING")
os.environ.setdefault("SUPABASE_URL", "https://mock.supabase.co")
os.environ.setdefault("SUPABASE_JWT_SECRET", "mock-secret-for-tests-1234567890")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/1")
os.environ.setdefault("ADMIN_EMAILS", "test@example.com")

# Trava de segurança: a suíte faz TRUNCATE nas tabelas. Nunca rodar contra o banco de dev/produção.
_db_name = os.environ["DATABASE_URL"].rsplit("/", 1)[-1].split("?")[0]
if not _db_name.endswith("_test"):
    raise SystemExit(
        f"ABORTADO: DATABASE_URL aponta para '{_db_name}', que não termina em '_test'. "
        "Os testes apagam dados. Rode com DATABASE_URL e DATABASE_URL_SYNC de um banco de teste."
    )


@pytest.fixture(scope="session", autouse=True)
def settle_limiter_singleton():
    """
    Monkeypatch do RateLimiter para evitar conflitos de loop e necessidade de Redis real.
    Gerencia o estado de 429 via GLOBAL_TEST_STATE para evitar patches assíncronos instáveis.
    """
    import fastapi_limiter.depends
    from fastapi import HTTPException

    async def _mock_call(self, request=None, response=None):
        if GLOBAL_TEST_STATE["force_429"]:
            GLOBAL_TEST_STATE["calls"] += 1
            if GLOBAL_TEST_STATE["calls"] > GLOBAL_TEST_STATE["429_threshold"]:
                raise HTTPException(status_code=429, detail="Too Many Requests")
        return None

    fastapi_limiter.depends.RateLimiter.__call__ = _mock_call


@pytest.fixture(autouse=True)
async def init_test_limiter():
    """Satisfaz a inicialização da biblioteca sem criar objetos bound ao loop."""
    mock_redis = AsyncMock()
    mock_redis.evalsha = AsyncMock(return_value=0)
    mock_redis.script_load = AsyncMock(return_value="sha")
    FastAPILimiter.redis = mock_redis
    yield
    FastAPILimiter.redis = None


@pytest.fixture(autouse=True)
def reset_test_state():
    """Reseta o estado global entre cada teste."""
    GLOBAL_TEST_STATE["force_429"] = False
    GLOBAL_TEST_STATE["calls"] = 0
    yield


@pytest.fixture(autouse=True)
def reset_breakers():
    """Garante que o estado do Circuit Breaker seja resetado entre os testes."""
    for breaker in CircuitBreakerMonitor.get_circuits():
        breaker.reset()


@pytest.fixture(autouse=True)
def override_auth(request):
    """Mocka a autenticação e verificação de assinatura globalmente para todos os testes."""
    from app.api.deps import get_current_user, require_active_subscription
    from app.main import app

    # Não aplicar override para testes de autenticação real (unitários)
    module_name = request.module.__name__
    if any(m in module_name for m in ["test_auth", "test_abacatepay_coverage"]):
        # Note: test_subscriptions precisa do override_auth por padrão para passar na maioria dos testes
        yield
        return

    async def _get_current_user_override():
        return {"email": "test@example.com", "sub": "test-uuid", "aud": "authenticated"}

    async def _require_active_subscription_override():
        return {"email": "test@example.com", "sub": "test-uuid", "aud": "authenticated"}

    app.dependency_overrides[get_current_user] = _get_current_user_override
    app.dependency_overrides[require_active_subscription] = _require_active_subscription_override
    yield
    app.dependency_overrides.pop(get_current_user, None)
    app.dependency_overrides.pop(require_active_subscription, None)


@pytest.fixture(scope="session", autouse=True)
async def create_test_tables():
    """
    Garante que todas as tabelas existem no banco de testes (incluindo as novas).
    Usa create_all para criar tabelas faltantes sem sobrescrever as existentes.
    """
    from sqlalchemy.ext.asyncio import create_async_engine

    from app.core.config import get_settings
    from app.persistence.models import Base

    settings = get_settings()
    engine = create_async_engine(settings.database_url, future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all, checkfirst=True)
    await engine.dispose()


@pytest.fixture(scope="function")
def db_engine():
    """Cria a engine assíncrona para a duração da sessão de testes."""
    from sqlalchemy.ext.asyncio import create_async_engine

    from app.core.config import get_settings

    settings = get_settings()
    engine = create_async_engine(settings.database_url, future=True)
    yield engine


@pytest.fixture
async def db_session(db_engine):
    """Fixture para fornecer uma sessão de banco de dados isolada para cada teste."""
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    session_factory = async_sessionmaker(
        bind=db_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    async with session_factory() as session:
        from sqlalchemy import text

        await session.execute(
            text(
                "TRUNCATE execution_annual, execution_monthly, remuneration_collected, "
                "user_subscription, subscription_plan, billing_transaction, support_message, "
                "senado_remuneracao, federal_remuneracao, person_latest, period_stat, estadual_remuneracao CASCADE"
            )
        )
        await session.commit()

        try:
            yield session
        finally:
            await session.rollback()
            await session.close()


@pytest.fixture
def override_get_session(db_session):
    """Override the get_session dependency with the test db_session."""
    from app.api.deps import get_session
    from app.main import app

    async def _get_session_override():
        yield db_session

    app.dependency_overrides[get_session] = _get_session_override
    yield
    app.dependency_overrides.pop(get_session, None)


@pytest.fixture
async def client():
    """Fixture para fornecer um cliente HTTP assíncrono para os testes da API."""
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture
def valid_token_headers():
    """
    Retorna headers de autenticação com token JWT mock.
    O override_auth já substitui get_current_user, então qualquer token funciona.
    O header é necessário para o HTTPBearer não rejeitar a requisição antes do override.
    """
    return {"Authorization": "Bearer mock-valid-token-for-tests"}


@pytest.fixture(autouse=True)
async def _limpa_cache_remuneration():
    """O cache Redis dos endpoints (busca, perfil, estatísticas) não pode vazar entre testes."""
    from app.core.cache import cache

    await cache.clear_prefix("remuneration:")
    yield
    await cache.clear_prefix("remuneration:")

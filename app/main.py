from contextlib import asynccontextmanager

import redis.asyncio as redis
import sentry_sdk
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi_limiter import FastAPILimiter

from app.api.router import api_router
from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

# Configuração do Sentry se DSN existir
if settings.sentry_dsn:
    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.app_env,
        traces_sample_rate=1.0 if settings.is_development else 0.1,
    )
    logger.info("Sentry inicializado com sucesso.")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lógica executada no ciclo de vida da aplicação."""
    # Inicializar Rate Limiter (Redis) — falha não deve derrubar o app
    r = None
    try:
        r = redis.from_url(settings.redis_url, encoding="utf-8", decode_responses=True)
        await FastAPILimiter.init(r)
        logger.info("Rate Limiter (FastAPI-Limiter) inicializado.")
    except Exception as e:
        logger.warning(
            f"Rate Limiter não pôde ser inicializado (Redis): {e}. Funcionando sem rate limit."
        )

    # Seed dos planos de assinatura
    await _seed_subscription_plans()

    yield

    # Limpeza se necessário
    if r:
        await r.aclose()


async def _seed_subscription_plans():
    """Insere os planos de assinatura padrão se ainda não existirem (idempotente)."""
    from sqlalchemy import select

    from app.persistence.models import SubscriptionPlan

    plans = [
        {
            "slug": "essencial",
            "name": "Plano Essencial",
            "description": (
                "Acesso completo ao inventário de remuneração 2020–2026 "
                "com atualizações diárias. Válido por 30 dias."
            ),
            "price_brl": 6.99,
            "duration_days": 30,
        },
        {
            "slug": "profissional",
            "name": "Plano Profissional",
            "description": (
                "Tudo do Essencial + exportações ilimitadas, sem rate-limit. Válido por 90 dias."
            ),
            "price_brl": 17.99,
            "duration_days": 90,
        },
        {
            "slug": "anual",
            "name": "Plano Anual",
            "description": (
                "Tudo do Profissional + acesso prioritário e suporte dedicado. "
                "Melhor custo-benefício. Válido por 365 dias."
            ),
            "price_brl": 49.99,
            "duration_days": 365,
        },
    ]

    try:
        from app.persistence.session import async_session_maker

        async with async_session_maker() as session:
            for plan_data in plans:
                stmt = select(SubscriptionPlan).where(SubscriptionPlan.slug == plan_data["slug"])
                result = await session.execute(stmt)
                if not result.scalar_one_or_none():
                    session.add(SubscriptionPlan(**plan_data))
                    logger.info(f"Plano '{plan_data['slug']}' criado.")
            await session.commit()
    except Exception as e:
        logger.warning(f"Seed de planos falhou (será tentado no próximo restart): {e}")


_IS_PRODUCTION = settings.app_env.lower() in ("production", "prod")

app = FastAPI(
    title="DF Remuneration Collector API",
    description="API para coleta e consulta de remuneração dos servidores do DF",
    version="0.3.0",
    lifespan=lifespan,
    # Em produção não publica a documentação interativa nem o schema (lista todas as rotas,
    # inclusive o prefixo do painel admin)
    docs_url=None if _IS_PRODUCTION else "/docs",
    redoc_url=None if _IS_PRODUCTION else "/redoc",
    openapi_url=None if _IS_PRODUCTION else "/openapi.json",
)

# Configurar CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "Accept", "X-Requested-With"],
)


@app.get("/health")
async def health_check():
    """Endpoint de health check para monitoramento."""
    return {
        "status": "ok",
        "env": settings.app_env,
        "version": app.version,
        "deployment_id": "FINAL_DEPLOY_V012",
    }


async def debug_routes():
    """Lista todas as rotas registradas no app (Apenas para debug)."""
    from starlette.routing import Mount, Route

    routes = []
    for route in app.routes:
        if isinstance(route, Route):
            routes.append({"path": route.path, "name": route.name, "methods": list(route.methods)})
        elif isinstance(route, Mount):
            routes.append({"path": route.path, "name": route.name, "methods": ["MOUNT"]})
    return routes


# Rota de diagnóstico: só existe fora de produção
if not _IS_PRODUCTION:
    app.add_api_route("/debug-routes", debug_routes, methods=["GET"])


# Registrar Rotas
app.include_router(api_router, prefix="/api/v1")

# Servir Dashboard Estático
app.mount("/dashboard", StaticFiles(directory="app/static", html=True), name="static")

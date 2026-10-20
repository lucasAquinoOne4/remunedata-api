"""
app/api/endpoints/remuneration.py
Endpoints para consulta de dados de remuneração coletados.
"""

from typing import Literal, Optional

from fastapi import APIRouter, Depends, Query
from fastapi_limiter.depends import RateLimiter

from app.api.deps import (
    get_remuneration_repository,
    get_senado_repository,
    require_active_subscription,
)
from app.api.schemas import PaginatedRemuneration
from app.core.cache import RedisCache, get_cache
from app.persistence.repositories import RemunerationRepository, SenadoRepository

router = APIRouter()


@router.get(
    "/",
    response_model=PaginatedRemuneration,
    dependencies=[Depends(RateLimiter(times=20, seconds=60))],
)
async def search_remuneration(
    nome: Optional[str] = Query(None, description="Parte do nome do servidor"),
    cpf: Optional[str] = Query(None, description="CPF do servidor (exato)"),
    cargo: Optional[str] = Query(None, description="Parte do cargo do servidor"),
    orgao: Optional[str] = Query(None, description="Parte do nome do órgão"),
    ano: Optional[int] = Query(None),
    mes: Optional[str] = Query(None),
    fonte: Literal["DF", "SENADO"] = Query(
        "DF", description="Origem dos dados: DF (Governo do DF) ou SENADO (Senado Federal)"
    ),
    page: int = Query(1, ge=1),
    size: int = Query(25, ge=1, le=200),
    repo: RemunerationRepository = Depends(get_remuneration_repository),
    senado_repo: SenadoRepository = Depends(get_senado_repository),
    user: dict = Depends(require_active_subscription),
):
    """Busca registros de remuneração com filtros e paginação."""
    offset = (page - 1) * size
    if fonte == "SENADO":
        items, total = await senado_repo.search(
            nome=nome, ano=ano, mes=mes, cargo=cargo, orgao=orgao, limit=size, offset=offset
        )
        pages = (total + size - 1) // size
        return {"items": items, "total": total, "page": page, "size": size, "pages": pages}

    items, total = await repo.search(
        nome=nome, cpf=cpf, cargo=cargo, orgao=orgao, ano=ano, mes=mes, limit=size, offset=offset
    )

    pages = (total + size - 1) // size

    return {"items": items, "total": total, "page": page, "size": size, "pages": pages}


@router.get("/distinct-filters")
async def get_distinct_filters(
    fonte: Literal["DF", "SENADO"] = Query("DF"),
    repo: RemunerationRepository = Depends(get_remuneration_repository),
    senado_repo: SenadoRepository = Depends(get_senado_repository),
    cache: RedisCache = Depends(get_cache),
):
    """Retorna listas únicas de cargos e órgãos para preencher os selects do frontend."""
    cache_key = (
        "remuneration:distinct_filters" if fonte == "DF" else "remuneration:distinct_filters:senado"
    )
    cached = await cache.get(cache_key)
    if cached:
        return cached

    # Busca do banco
    data = await (senado_repo if fonte == "SENADO" else repo).get_distinct_filters()

    # Cache de 24 horas (valores mudam pouco)
    await cache.set(cache_key, data, ttl=86400)
    return data


# Dependência nomeada para facilitar overrides em testes.
# Usamos uma função wrapper para que o override via dependency_overrides funcione corretamente.
async def summary_limiter(request=None, response=None):
    """
    Wrapper de rate-limiting para o endpoint /summary.
    Tolerante a falha: se o Redis não estiver disponível, apenas ignora o rate-limit
    em vez de retornar 500 e quebrar o CORS.
    """
    try:
        limiter = RateLimiter(times=30, seconds=60)
        await limiter(request=request, response=response)
    except Exception:
        # FastAPILimiter não inicializado (Redis indisponível) — degrada graciosamente
        pass


@router.get("/summary", dependencies=[Depends(summary_limiter)])
async def get_summary(
    ano: Optional[int] = Query(None),
    repo: RemunerationRepository = Depends(get_remuneration_repository),
    cache: RedisCache = Depends(get_cache),
):
    """Retorna dados agregados para o dashboard com cache Redis de 10 minutos."""
    cache_key = f"remuneration:summary:{ano or 'all'}"
    cached = await cache.get(cache_key)
    if cached:
        return cached

    # Caso contrário, busca do banco
    data = await repo.get_summary(ano=ano)

    # Atualiza cache (10 minutos)
    await cache.set(cache_key, data, ttl=600)

    return data

"""
app/api/endpoints/remuneration.py
Endpoints para consulta de dados de remuneração coletados.
"""

import hashlib
import io
import json
from typing import Literal, Optional

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.encoders import jsonable_encoder
from fastapi.responses import StreamingResponse
from fastapi_limiter.depends import RateLimiter
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    get_estadual_repository_factory,
    get_federal_repository,
    get_remuneration_repository,
    get_senado_repository,
    get_session,
    require_active_subscription,
)
from app.api.schemas import PaginatedRemuneration
from app.core.cache import RedisCache, get_cache
from app.persistence.repositories import (
    COUNT_CAP,
    FederalRepository,
    PersonLatestRepository,
    RemunerationRepository,
    SenadoRepository,
)
from app.services import people
from app.services.people import norm_name

Fonte = Literal["DF", "SENADO", "FEDERAL", "CE", "PE", "GO", "SC"]

router = APIRouter()


def _cache_key(prefix: str, **params) -> str:
    """Chave de cache estável a partir dos parâmetros (inclui só valores não vazios)."""
    raw = json.dumps({k: v for k, v in params.items() if v not in (None, "")}, sort_keys=True)
    return f"remuneration:{prefix}:{hashlib.sha1(raw.encode()).hexdigest()[:20]}"


async def _snapshots(session: AsyncSession, cache: RedisCache) -> dict:
    """
    Competência do retrato por pessoa de cada fonte (só existe se o retrato foi construído).
    A busca só usa o caminho rápido quando o retrato existe.
    """
    data = await cache.get("remuneration:snapshots")
    if data is None:
        data = await people.snapshots(session)
        await cache.set("remuneration:snapshots", data, ttl=600)
    return data


def _repo_for(fonte: str, df, senado, federal, estadual=None):
    if fonte in ("DF", "SENADO", "FEDERAL"):
        return {"DF": df, "SENADO": senado, "FEDERAL": federal}[fonte]
    return estadual(fonte)


@router.get(
    "/",
    response_model=PaginatedRemuneration,
    dependencies=[Depends(RateLimiter(times=120, seconds=60))],
)
async def search_remuneration(
    nome: Optional[str] = Query(None, description="Parte do nome do servidor"),
    cpf: Optional[str] = Query(None, description="CPF do servidor (exato; só fonte DF)"),
    cargo: Optional[str] = Query(None, description="Cargo do servidor"),
    orgao: Optional[str] = Query(None, description="Órgão"),
    ano: Optional[int] = Query(None),
    mes: Optional[str] = Query(None),
    fonte: Fonte = Query(
        "DF", description="Origem: DF (Governo do DF), SENADO (Senado) ou FEDERAL (Executivo)"
    ),
    page: int = Query(1, ge=1),
    size: int = Query(25, ge=1, le=200),
    repo: RemunerationRepository = Depends(get_remuneration_repository),
    senado_repo: SenadoRepository = Depends(get_senado_repository),
    federal_repo: FederalRepository = Depends(get_federal_repository),
    estadual=Depends(get_estadual_repository_factory),
    session: AsyncSession = Depends(get_session),
    cache: RedisCache = Depends(get_cache),
    user: dict = Depends(require_active_subscription),
):
    """Busca registros de remuneração com filtros e paginação (cacheada por 5 min)."""
    key = _cache_key(
        "search",
        nome=nome,
        cpf=cpf,
        cargo=cargo,
        orgao=orgao,
        ano=ano,
        mes=mes,
        fonte=fonte,
        page=page,
        size=size,
    )
    cached = await cache.get(key)
    if cached:
        return cached

    offset = (page - 1) * size
    latest = None
    if not cpf:
        latest = (await _snapshots(session, cache)).get(fonte)
    # Caminho rápido: sem período (ou no mais recente) a busca usa o retrato por pessoa, uma
    # tabela pequena com índice trigram em memória, em vez das tabelas mensais.
    use_latest = bool(
        latest
        and (nome or cargo or orgao)
        and (ano is None or ano == latest["ano"])
        and (mes is None or int(mes) == int(latest["mes"]))
    )
    if use_latest:
        items, total = await PersonLatestRepository(session).search(
            fonte, nome=nome, cargo=cargo, orgao=orgao, limit=size, offset=offset
        )
    elif fonte == "DF":
        items, total = await repo.search(
            nome=nome,
            cpf=cpf,
            cargo=cargo,
            orgao=orgao,
            ano=ano,
            mes=mes,
            limit=size,
            offset=offset,
        )
    else:
        target = _repo_for(fonte, repo, senado_repo, federal_repo, estadual)
        items, total = await target.search(
            nome=nome, ano=ano, mes=mes, cargo=cargo, orgao=orgao, limit=size, offset=offset
        )

    capped = total > COUNT_CAP
    total = min(total, COUNT_CAP)
    pages = (total + size - 1) // size
    result = jsonable_encoder(
        {
            "items": items,
            "total": total,
            "total_capped": capped,
            "page": page,
            "size": size,
            "pages": pages,
        }
    )
    await cache.set(key, result, ttl=300)
    return result


_EXPORT_COLUMNS = [
    ("fonte", "Fonte"),
    ("ano_exercicio", "Ano"),
    ("mes_referencia", "Mês"),
    ("nome_servidor", "Nome"),
    ("cpf_servidor", "CPF (mascarado)"),
    ("codigo_matricula", "Matrícula"),
    ("nome_orgao", "Órgão"),
    ("cargo", "Cargo"),
    ("valor_bruto", "Remuneração bruta (R$)"),
    ("valor_liquido", "Remuneração líquida (R$)"),
]


@router.get("/export", dependencies=[Depends(RateLimiter(times=10, seconds=60))])
async def export_search(
    formato: Literal["csv", "xlsx"] = Query("csv"),
    nome: Optional[str] = Query(None),
    cargo: Optional[str] = Query(None),
    orgao: Optional[str] = Query(None),
    ano: Optional[int] = Query(None),
    mes: Optional[str] = Query(None),
    fonte: Fonte = Query("DF"),
    repo: RemunerationRepository = Depends(get_remuneration_repository),
    senado_repo: SenadoRepository = Depends(get_senado_repository),
    federal_repo: FederalRepository = Depends(get_federal_repository),
    estadual=Depends(get_estadual_repository_factory),
    user: dict = Depends(require_active_subscription),
):
    """
    Exporta o resultado da busca atual em CSV (até 5.000 linhas) ou XLSX (até 1.000), conforme
    os limites de segurança do projeto. Recurso de assinante; exige ao menos um filtro.
    """
    if not any([nome, cargo, orgao]):
        raise HTTPException(status_code=422, detail="Informe nome, cargo ou órgão para exportar.")
    limit = 1000 if formato == "xlsx" else 5000
    if fonte == "DF":
        raw, _ = await repo.search(
            nome=nome, cargo=cargo, orgao=orgao, ano=ano, mes=mes, limit=limit, offset=0
        )
        items = [
            {
                "fonte": "DF",
                "ano_exercicio": r.ano_exercicio,
                "mes_referencia": r.mes_referencia,
                "nome_servidor": r.nome_servidor,
                "cpf_servidor": r.cpf_servidor,
                "codigo_matricula": r.codigo_matricula,
                "nome_orgao": r.nome_orgao,
                "cargo": r.cargo,
                "valor_bruto": r.valor_bruto,
                "valor_liquido": r.valor_liquido,
            }
            for r in raw
        ]
    else:
        items, _ = await _repo_for(fonte, repo, senado_repo, federal_repo, estadual).search(
            nome=nome, ano=ano, mes=mes, cargo=cargo, orgao=orgao, limit=limit, offset=0
        )
    frame = pd.DataFrame(
        [{label: row.get(key) for key, label in _EXPORT_COLUMNS} for row in items],
        columns=[label for _, label in _EXPORT_COLUMNS],
    )
    stem = f"remunedata_{fonte.lower()}"
    if formato == "csv":
        # ';' e vírgula decimal (Excel pt-BR) e BOM para acentos
        content = frame.to_csv(index=False, sep=";", decimal=",").encode("utf-8-sig")
        return StreamingResponse(
            io.BytesIO(content),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{stem}.csv"'},
        )
    buffer = io.BytesIO()
    frame.to_excel(buffer, index=False, engine="openpyxl")
    buffer.seek(0)
    return StreamingResponse(
        buffer,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{stem}.xlsx"'},
    )


@router.get("/distinct-filters")
async def get_distinct_filters(
    fonte: Fonte = Query("DF"),
    repo: RemunerationRepository = Depends(get_remuneration_repository),
    senado_repo: SenadoRepository = Depends(get_senado_repository),
    federal_repo: FederalRepository = Depends(get_federal_repository),
    estadual=Depends(get_estadual_repository_factory),
    cache: RedisCache = Depends(get_cache),
):
    """Retorna listas únicas de cargos e órgãos para preencher os selects do frontend."""
    cache_key = (
        "remuneration:distinct_filters"
        if fonte == "DF"
        else f"remuneration:distinct_filters:{fonte.lower()}"
    )
    cached = await cache.get(cache_key)
    if cached:
        return cached

    data = await _repo_for(fonte, repo, senado_repo, federal_repo, estadual).get_distinct_filters()

    # Cache de 24 horas (valores mudam pouco)
    await cache.set(cache_key, data, ttl=86400)
    return data


@router.get("/periods", dependencies=[Depends(RateLimiter(times=60, seconds=60))])
async def get_periods(
    session: AsyncSession = Depends(get_session),
    cache: RedisCache = Depends(get_cache),
    user: dict = Depends(require_active_subscription),
):
    """Competências carregadas por fonte (a tela usa a mais recente como padrão)."""
    key = "remuneration:periods"
    cached = await cache.get(key)
    if cached:
        return cached
    data = {fonte: await people.periods(session, fonte) for fonte in people.SOURCES}
    await cache.set(key, data, ttl=21600)
    return data


@router.get("/suggest", dependencies=[Depends(RateLimiter(times=120, seconds=60))])
async def get_suggestions(
    q: str = Query(..., min_length=3, max_length=60),
    fonte: Optional[Fonte] = Query(None),
    session: AsyncSession = Depends(get_session),
    cache: RedisCache = Depends(get_cache),
    user: dict = Depends(require_active_subscription),
):
    """Sugestões de nomes para a pesquisa dinâmica (mês mais recente de cada fonte)."""
    key = _cache_key("suggest", q=norm_name(q), fonte=fonte)
    cached = await cache.get(key)
    if cached:
        return cached
    fontes = [fonte] if fonte else list(people.SOURCES)
    periods = await get_periods(session=session, cache=cache, user=user)
    found = []
    for f in fontes:
        latest = (periods.get(f) or [None])[0]
        found += await people.suggest(session, f, q, latest)
    data = {"suggestions": found[:8] if fonte else _interleave(found)}
    await cache.set(key, data, ttl=600)
    return data


def _interleave(items: list) -> list:
    """Até 8 sugestões alternando as fontes (para 'Todas' mostrar mistura)."""
    by_source: dict = {}
    for item in items:
        by_source.setdefault(item["fonte"], []).append(item)
    out, i = [], 0
    while len(out) < 8 and any(by_source.values()):
        for lst in by_source.values():
            if i < len(lst) and len(out) < 8:
                out.append(lst[i])
        i += 1
        if i > 8:
            break
    return out


@router.get("/stats", dependencies=[Depends(RateLimiter(times=60, seconds=60))])
async def get_stats(
    fonte: Fonte = Query("DF"),
    ano: Optional[int] = Query(None, ge=2000, le=2100),
    session: AsyncSession = Depends(get_session),
    cache: RedisCache = Depends(get_cache),
    user: dict = Depends(require_active_subscription),
):
    """Agregados para os gráficos do dashboard (cacheados por 1 hora)."""
    key = _cache_key("stats", fonte=fonte, ano=ano)
    cached = await cache.get(key)
    if cached:
        return cached
    data = await people.stats(session, fonte, ano)
    await cache.set(key, data, ttl=3600)
    return data


@router.get("/person", dependencies=[Depends(RateLimiter(times=60, seconds=60))])
async def get_person(
    fonte: Fonte = Query(...),
    id: str = Query(..., min_length=1, max_length=40),
    matricula: Optional[str] = Query(None, max_length=40),
    session: AsyncSession = Depends(get_session),
    cache: RedisCache = Depends(get_cache),
    user: dict = Depends(require_active_subscription),
):
    """
    Perfil enriquecido: identidade, histórico mês a mês com todos os componentes, comparação com
    pares, cruzamento provável com as outras fontes e, para o Executivo federal, a ficha da API
    do portal (uma chamada por pessoa, cacheada por 7 dias).
    """
    key = _cache_key("person", fonte=fonte, id=id, matricula=matricula)
    data = await cache.get(key)
    if data is None:
        data = await people.person(session, fonte, id, matricula)
        if data is None:
            raise HTTPException(status_code=404, detail="Pessoa não encontrada.")
        await cache.set(key, data, ttl=600)

    if fonte == "FEDERAL" and data.get("ficha_federal") is None:
        ficha_key = f"remuneration:ficha:{id}"
        ficha = await cache.get(ficha_key)
        if ficha is None:
            ficha = await people.federal_ficha(id)
            if ficha:
                await cache.set(ficha_key, ficha, ttl=7 * 86400)
        data = {**data, "ficha_federal": ficha}
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
    ano: Optional[int] = Query(None, ge=2000, le=2100),
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

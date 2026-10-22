"""
app/workers/tasks.py
Definição de tarefas assíncronas do Celery.
"""

import asyncio

from celery.utils.log import get_task_logger

from app.collector.annual import AnnualCollector
from app.collector.estadual import EstadualCollector
from app.collector.federal import FederalCollector
from app.collector.monthly import MonthlyCollector
from app.collector.senado import SenadoCollector
from app.core import locks
from app.core.cache import cache
from app.core.celery_app import celery_app
from app.core.config import get_settings
from app.infra.federal_client import DATASETS, FederalDownloadClient
from app.infra.senado_client import SenadoClient
from app.infra.transparencia_client import TransparenciaClient
from app.persistence.repositories import (
    EstadualRepository,
    ExecutionRepository,
    FederalRepository,
    RemunerationRepository,
    SenadoRepository,
)
from app.persistence.session import async_engine, async_session_maker

logger = get_task_logger(__name__)


@celery_app.task(name="collect_annual_task", bind=True)
def collect_annual_task(self, ano: int):
    """
    Tarefa Celery para executar a coleta anual completa.
    Wrapper síncrono para a execução assíncrona dos coletores.
    """
    owner = str(self.request.id)
    if not locks.acquire(locks.DF_COLLECT_LOCK, owner):
        # Só uma coleta do DF por vez (o portal limita a taxa por IP). Tenta de novo em 5 min.
        logger.info(
            f"Coleta do DF já em andamento ({locks.current_owner(locks.DF_COLLECT_LOCK)}). "
            f"Ano {ano} reagendado."
        )
        raise self.retry(countdown=300, max_retries=288)

    logger.info(f"Iniciando tarefa de coleta anual: {ano} (Task ID: {self.request.id})")

    async def _run():
        async with async_session_maker() as session:
            # Setup dependências
            client = TransparenciaClient()
            exec_repo = ExecutionRepository(session)
            rem_repo = RemunerationRepository(session)

            monthly_collector = MonthlyCollector(client, exec_repo, rem_repo)
            annual_collector = AnnualCollector(monthly_collector, exec_repo)

            # Executar
            result = await annual_collector.run(ano)

            # Limpar cache do dashboard/filtros após sucesso
            if result.status in ["success", "partial_success"]:
                logger.info(f"Limpando cache de remuneração para o ano {ano}")
                await cache.clear_prefix("remuneration:")

            # Extrair dados básicos antes de fechar a sessão/loop para evitar DetachedInstanceError
            return {"status": result.status, "execution_id": result.id}

    try:
        # Celery roda em processos síncronos, precisamos do loop para o código async
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            task_result = loop.run_until_complete(_run())
            logger.info(
                f"Tarefa de coleta anual {ano} concluída com status: {task_result['status']}"
            )
            return task_result
        finally:
            # Crucial: Limpar todas as conexões do pool ANTES de fechar o loop
            # Isso evita o erro "Event loop is closed" em tarefas subsequentes
            loop.run_until_complete(async_engine.dispose())
            loop.close()
    except Exception as e:
        from celery.exceptions import SoftTimeLimitExceeded

        if isinstance(e, SoftTimeLimitExceeded):
            logger.warning(
                f"Limite suave de tempo atingido para coleta {ano}. A tarefa será encerrada em breve."
            )
        else:
            logger.error(f"Erro fatal na tarefa de coleta {ano}: {str(e)}")
        raise e
    finally:
        locks.release(locks.DF_COLLECT_LOCK, owner)


@celery_app.task(name="sync_recent_years_task")
def sync_recent_years_task():
    """
    Tarefa periódica para sincronizar o ano atual e o anterior.
    """
    from datetime import datetime

    current_year = datetime.now().year
    previous_year = current_year - 1

    # Se já há coleta do DF em andamento, não empilha outra (evita tarefas duplicadas na fila)
    running = locks.current_owner(locks.DF_COLLECT_LOCK)
    if running:
        logger.info(f"Sincronização do DF ignorada: coleta {running} já em andamento.")
        return {"status": "skipped", "running": running}

    logger.info(f"Iniciando sincronização periódica: {previous_year} e {current_year}")

    # Ano atual primeiro (dados mais procurados); a trava global serializa a execução
    collect_annual_task.delay(current_year)
    collect_annual_task.delay(previous_year)

    return {"status": "queued", "years": [previous_year, current_year]}


@celery_app.task(name="retry_monthly_task", bind=True)
def retry_monthly_task(self, execution_id: int, mes: str):
    """
    Tarefa Celery para re-executar a coleta de um mês específico que falhou.
    """
    logger.info(f"Iniciando retentativa manual: Mês {mes} (Execução: {execution_id})")

    async def _run():
        async with async_session_maker() as session:
            client = TransparenciaClient()
            exec_repo = ExecutionRepository(session)
            rem_repo = RemunerationRepository(session)

            # 1. Carregar o registro anual para obter o ano
            annual = await exec_repo.get_annual(execution_id)
            if not annual:
                raise ValueError(f"Execução anual {execution_id} não encontrada.")

            # 2. Executar coleta apenas daquele mês
            collector = MonthlyCollector(client, exec_repo, rem_repo)
            result = await collector.collect(
                ano=annual.ano_exercicio, mes=mes, annual_execution_id=execution_id
            )

            # 3. Sincronizar status anual
            await exec_repo.sync_annual_stats(execution_id)

            # Limpar cache
            await cache.clear_prefix("remuneration:")

            return {"status": result.status, "execution_id": execution_id, "mes": mes}

    # Garantir loop isolado e limpeza do pool para evitar conflitos no Celery
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(_run())
    finally:
        loop.run_until_complete(async_engine.dispose())
        loop.close()


def _recent_months(n: int) -> list[tuple[int, int]]:
    """Os n meses mais recentes (inclui o atual), do mais novo ao mais antigo."""
    from datetime import datetime

    now = datetime.now()
    year, month = now.year, now.month
    result = []
    for _ in range(n):
        result.append((year, month))
        month -= 1
        if month == 0:
            year, month = year - 1, 12
    return result


@celery_app.task(name="collect_senado_month_task", bind=True)
def collect_senado_month_task(self, ano: int, mes: int):
    """Coleta a folha de um mês do Senado Federal (idempotente)."""
    logger.info(f"Iniciando coleta do Senado {mes:02d}/{ano} (Task ID: {self.request.id})")
    locks.set_running("SENADO", True)

    async def _run():
        async with async_session_maker() as session:
            collector = SenadoCollector(SenadoClient(), SenadoRepository(session))
            result = await collector.collect_month(ano, mes)
            if result["registros"]:
                await cache.clear_prefix("remuneration:")
            return result

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(_run())
    finally:
        loop.run_until_complete(async_engine.dispose())
        loop.close()
        locks.set_running("SENADO", False)


@celery_app.task(name="sync_senado_task")
def sync_senado_task():
    """Sincronização periódica: revisita os últimos meses do Senado."""
    months = _recent_months(get_settings().senado_sync_months)
    for ano, mes in months:
        collect_senado_month_task.delay(ano, mes)
    return {"status": "queued", "months": [f"{a}-{m:02d}" for a, m in months]}


@celery_app.task(name="collect_federal_month_task", bind=True)
def collect_federal_month_task(
    self, ano: int, mes: int, force: bool = False, categoria: str = "CIVIL"
):
    """
    Carrega um mês do Executivo federal (ZIP do Portal da Transparência). Um mês já carregado é
    pulado sem nenhuma requisição, a menos que force=True.
    """
    logger.info(
        f"Iniciando carga federal {categoria} {mes:02d}/{ano} (force={force}, Task {self.request.id})"
    )
    locks.set_running("FEDERAL", True)

    async def _run():
        async with async_session_maker() as session:
            collector = FederalCollector(FederalDownloadClient(), FederalRepository(session))
            result = await collector.collect_month(ano, mes, force=force, categoria=categoria)
            if result["registros"]:
                await cache.clear_prefix("remuneration:")
            return result

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(_run())
    finally:
        loop.run_until_complete(async_engine.dispose())
        loop.close()
        locks.set_running("FEDERAL", False)


@celery_app.task(name="sync_federal_task")
def sync_federal_task():
    """
    Verificação semanal do Executivo federal (civil, militar e Banco Central). Para cada categoria,
    percorre os últimos meses do mais recente para o mais antigo e garante que os
    `FEDERAL_SYNC_MONTHS` meses mais recentes PUBLICADOS estejam carregados. Mês já carregado não
    gera requisição; mês não publicado custa um HEAD.
    """
    wanted = get_settings().federal_sync_months

    async def _plan():
        client = FederalDownloadClient()
        queued = []
        async with async_session_maker() as session:
            repo = FederalRepository(session)
            for categoria in DATASETS:
                loaded = 0
                for ano, mes in _recent_months(wanted + 4):
                    if loaded >= wanted:
                        break
                    if await repo.month_loaded(ano, mes, categoria):
                        loaded += 1
                    elif await client.is_published(ano, mes, categoria):
                        queued.append((ano, mes, categoria))
                        loaded += 1
        return queued

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        queued = loop.run_until_complete(_plan())
    finally:
        loop.run_until_complete(async_engine.dispose())
        loop.close()
    for ano, mes, categoria in queued:
        collect_federal_month_task.delay(ano, mes, False, categoria)
    return {"status": "queued", "months": [f"{a}-{m:02d}:{c}" for a, m, c in queued]}


@celery_app.task(name="refresh_person_latest_task")
def refresh_person_latest_task(fonte: str | None = None):
    """Reconstrói o retrato por pessoa (todas as fontes, ou só uma)."""
    from app.services import people

    async def _run():
        out = {}
        async with async_session_maker() as session:
            for f in [fonte] if fonte else list(people.SOURCES):
                out[f] = await people.refresh_person_latest(session, f)
        await cache.clear_prefix("remuneration:")
        return out

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(_run())
    finally:
        loop.run_until_complete(async_engine.dispose())
        loop.close()


@celery_app.task(name="warm_portal_cache_task")
def warm_portal_cache_task():
    """
    Pré-aquece o que a primeira consulta do dia pagaria caro numa máquina pequena: competências e
    gráficos das três fontes. Roda depois que a instância liga (05:30) e após cargas.
    """
    from app.services import people

    async def _run():
        async with async_session_maker() as session:
            data = {f: await people.periods(session, f) for f in people.SOURCES}
            await cache.set("remuneration:periods", data, ttl=21600)
            for f in people.SOURCES:
                stats = await people.stats(session, f, None)
                import hashlib
                import json

                raw = json.dumps({"fonte": f}, sort_keys=True)
                key = f"remuneration:stats:{hashlib.sha1(raw.encode()).hexdigest()[:20]}"
                await cache.set(key, stats, ttl=3600)
        return {f: len(v) for f, v in data.items()}

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(_run())
    finally:
        loop.run_until_complete(async_engine.dispose())
        loop.close()


@celery_app.task(name="collect_estadual_month_task", bind=True)
def collect_estadual_month_task(self, uf: str, ano: int, mes: int, force: bool = False):
    """Carrega um mês de um estado pelo adaptador de fonte (app/sources)."""
    from app.sources import ADAPTERS

    adapter = ADAPTERS.get(uf.upper())
    if adapter is None:
        raise ValueError(f"Sem adaptador para a UF {uf}")
    logger.info(f"Iniciando carga {uf} {mes:02d}/{ano} (force={force}, Task {self.request.id})")
    locks.set_running(uf.upper(), True)

    async def _run():
        async with async_session_maker() as session:
            collector = EstadualCollector(adapter, EstadualRepository(session, uf))
            result = await collector.collect_month(ano, mes, force=force)
            if result["registros"]:
                await cache.clear_prefix("remuneration:")
            return result

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(_run())
    finally:
        loop.run_until_complete(async_engine.dispose())
        loop.close()
        locks.set_running(uf.upper(), False)


@celery_app.task(name="sync_estadual_task")
def sync_estadual_task():
    """
    Verificação semanal dos estados: para cada adaptador, garante os `ESTADUAL_SYNC_MONTHS` meses
    mais recentes PUBLICADOS. Mês já carregado não gera requisição.
    """
    from app.sources import ADAPTERS

    wanted = get_settings().estadual_sync_months

    async def _plan():
        queued = []
        async with async_session_maker() as session:
            for uf, adapter in ADAPTERS.items():
                repo = EstadualRepository(session, uf)
                loaded = 0
                try:
                    for ano, mes in _recent_months(wanted + 4):
                        if loaded >= wanted:
                            break
                        if await repo.month_loaded(ano, mes):
                            loaded += 1
                        elif await adapter.is_published(ano, mes):
                            queued.append((uf, ano, mes))
                            loaded += 1
                except Exception as exc:  # uma fonte fora do ar ou bloqueada não para as outras
                    logger.warning(f"Verificação de {uf} ignorada nesta semana: {exc}")
        return queued

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        queued = loop.run_until_complete(_plan())
    finally:
        loop.run_until_complete(async_engine.dispose())
        loop.close()
    for uf, ano, mes in queued:
        collect_estadual_month_task.delay(uf, ano, mes)
    return {"status": "queued", "months": [f"{u}:{a}-{m:02d}" for u, a, m in queued]}


@celery_app.task(name="refresh_economia_task")
def refresh_economia_task():
    """Atualiza IPCA, salário mínimo e Selic (IBGE SIDRA e BCB SGS): 3 requisições por execução."""
    from app.services import economia

    async def _run():
        async with async_session_maker() as session:
            count = await economia.refresh(session)
        await cache.clear_prefix("remuneration:person")
        return count

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(_run())
    finally:
        loop.run_until_complete(async_engine.dispose())
        loop.close()

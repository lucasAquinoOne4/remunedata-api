"""
app/workers/tasks.py
Definição de tarefas assíncronas do Celery.
"""

import asyncio

from celery.utils.log import get_task_logger

from app.collector.annual import AnnualCollector
from app.collector.monthly import MonthlyCollector
from app.collector.senado import SenadoCollector
from app.core import locks
from app.core.cache import cache
from app.core.celery_app import celery_app
from app.core.config import get_settings
from app.infra.senado_client import SenadoClient
from app.infra.transparencia_client import TransparenciaClient
from app.persistence.repositories import (
    ExecutionRepository,
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


@celery_app.task(name="sync_senado_task")
def sync_senado_task():
    """Sincronização periódica: revisita os últimos meses do Senado."""
    months = _recent_months(get_settings().senado_sync_months)
    for ano, mes in months:
        collect_senado_month_task.delay(ano, mes)
    return {"status": "queued", "months": [f"{a}-{m:02d}" for a, m in months]}

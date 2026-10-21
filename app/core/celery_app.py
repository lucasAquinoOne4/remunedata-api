"""
app/core/celery_app.py
Configuração central do Celery.
"""

from celery import Celery
from celery.schedules import crontab

from app.core.config import settings

celery_app = Celery(
    "df_remuneration_worker",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["app.workers.tasks"],
)

# Configurações adicionais
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="America/Sao_Paulo",
    enable_utc=True,
    task_track_started=True,
    task_time_limit=86400,  # 24 horas (limite máximo para coleta massiva)
    task_soft_time_limit=82800,  # 23 horas (aviso para finalização graciosa)
)

celery_app.conf.beat_schedule = {
    "sync-recent-years-daily": {
        "task": "sync_recent_years_task",
        "schedule": crontab(hour=6, minute=0),  # Diário às 06:00 (instância liga às 05:30)
    },
    "sync-federal-weekly": {
        "task": "sync_federal_task",
        # Uma verificação por semana (quarta 08:00): o portal publica o mês anterior por volta
        # do dia 9-10 e os dados mudam pouco. Meses já carregados não geram requisição.
        "schedule": crontab(hour=8, minute=0, day_of_week="wed"),
    },
    "sync-senado-daily": {
        "task": "sync_senado_task",
        "schedule": crontab(hour=7, minute=0),  # Diário às 07:00 (instância liga às 05:30)
    },
}


from celery.signals import worker_ready  # noqa: E402


@worker_ready.connect
def _clear_stale_locks(**_kwargs):
    """Se o worker caiu no meio de uma coleta, a trava ficaria órfã até o TTL: limpa ao subir."""
    import asyncio

    from app.core import locks
    from app.core.logging import get_logger

    locks.clear(locks.DF_COLLECT_LOCK)

    # Execuções que ficaram 'running' porque o worker parou no meio (ex.: desligamento às 01:00)
    async def _mark_interrupted() -> int:
        from app.persistence.repositories import ExecutionRepository
        from app.persistence.session import async_engine, async_session_maker

        try:
            async with async_session_maker() as session:
                return await ExecutionRepository(session).mark_interrupted()
        finally:
            await async_engine.dispose()

    try:
        count = asyncio.run(_mark_interrupted())
        get_logger(__name__).info(f"Execuções interrompidas marcadas na subida do worker: {count}")
    except Exception as e:  # nunca impede o worker de subir
        get_logger(__name__).warning(f"Não foi possível marcar execuções interrompidas: {e}")

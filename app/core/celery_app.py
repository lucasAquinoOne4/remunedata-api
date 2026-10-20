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
    "sync-senado-daily": {
        "task": "sync_senado_task",
        "schedule": crontab(hour=7, minute=0),  # Diário às 07:00 (instância liga às 05:30)
    },
}


from celery.signals import worker_ready  # noqa: E402


@worker_ready.connect
def _clear_stale_locks(**_kwargs):
    """Se o worker caiu no meio de uma coleta, a trava ficaria órfã até o TTL: limpa ao subir."""
    from app.core import locks

    locks.clear(locks.DF_COLLECT_LOCK)

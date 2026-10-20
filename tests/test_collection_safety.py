"""
tests/test_collection_safety.py
Proteções da coleta: sem duplicatas no banco e uma coleta do DF por vez.
"""

import uuid

import pytest
from sqlalchemy import func, select

from app.core import locks
from app.persistence.models import RemunerationCollected
from app.persistence.repositories import ExecutionRepository, RemunerationRepository


def _item(execution_id, monthly_id, ano, mes, ident, payload):
    return RemunerationCollected(
        execution_id=execution_id,
        monthly_execution_id=monthly_id,
        ano_exercicio=ano,
        mes_referencia=mes,
        codigo_identificacao=ident,
        codigo_matricula="M",
        nome_servidor="SERVIDOR SEGURANCA",
        raw_payload_json=payload,
    )


@pytest.mark.asyncio
async def test_save_batch_ignora_duplicatas_exatas(db_session):
    exec_repo = ExecutionRepository(db_session)
    repo = RemunerationRepository(db_session)
    annual = await exec_repo.get_or_create_annual(2097)
    monthly = await exec_repo.create_monthly(annual.id, "03")

    payload = f'{{"id": "{uuid.uuid4()}"}}'
    batch = [_item(annual.id, monthly.id, 2097, "03", "DUP_1", payload)]

    await repo.save_batch(batch)
    # Segunda coleta (ou coleta concorrente) do mesmo registro: não pode duplicar
    await repo.save_batch([_item(annual.id, monthly.id, 2097, "03", "DUP_1", payload)])
    # Registro diferente continua entrando
    await repo.save_batch(
        [_item(annual.id, monthly.id, 2097, "03", "DUP_2", f'{{"id": "{uuid.uuid4()}"}}')]
    )

    total = (
        await db_session.execute(
            select(func.count())
            .select_from(RemunerationCollected)
            .where(RemunerationCollected.ano_exercicio == 2097)
        )
    ).scalar_one()
    assert total == 2


def test_trava_de_coleta_df_e_exclusiva():
    key = "lock:test:coleta"
    locks.clear(key)
    assert locks.acquire(key, "task-a") is True
    assert locks.acquire(key, "task-b") is False  # segunda coleta é barrada
    locks.release(key, "task-b")  # quem não é dono não consegue soltar
    assert locks.current_owner(key) == "task-a"
    locks.release(key, "task-a")
    assert locks.acquire(key, "task-b") is True  # liberada, outra pode assumir
    locks.clear(key)


def test_ttl_da_trava_cobre_coleta_longa():
    # A coleta dura horas; a trava não pode expirar antes do limite de 24 h da tarefa
    assert locks.DEFAULT_TTL_SECONDS > 24 * 3600


def test_sync_do_df_nao_empilha_quando_ja_ha_coleta(monkeypatch):
    from app.workers import tasks

    enfileiradas = []
    monkeypatch.setattr(tasks.collect_annual_task, "delay", lambda ano: enfileiradas.append(ano))
    locks.clear(locks.DF_COLLECT_LOCK)
    locks.acquire(locks.DF_COLLECT_LOCK, "em-andamento")
    try:
        result = tasks.sync_recent_years_task()
        assert result["status"] == "skipped"
        assert enfileiradas == []
    finally:
        locks.clear(locks.DF_COLLECT_LOCK)

    result = tasks.sync_recent_years_task()
    assert result["status"] == "queued"
    assert len(enfileiradas) == 2

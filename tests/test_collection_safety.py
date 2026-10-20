"""
tests/test_collection_safety.py
Proteções da coleta: sem duplicatas no banco e uma coleta do DF por vez.
"""

import uuid
from pathlib import Path

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


@pytest.mark.asyncio
async def test_mark_interrupted_fecha_execucoes_orfas(db_session):

    exec_repo = ExecutionRepository(db_session)
    annual = await exec_repo.get_or_create_annual(2096)
    annual.status = "running"
    annual.total_meses_processados = 2
    monthly = await exec_repo.create_monthly(annual.id, "05")
    assert monthly.status == "running"
    await db_session.commit()

    count = await exec_repo.mark_interrupted()
    assert count >= 2

    await db_session.refresh(annual)
    await db_session.refresh(monthly)
    assert monthly.status == "error" and "Interrompida" in monthly.error_message
    assert monthly.finished_at is not None
    assert annual.status == "partial_success"  # já tinha mês concluído

    # sem nenhum mês concluído vira 'error'
    vazio = await exec_repo.get_or_create_annual(2095)
    vazio.status = "running"
    vazio.total_meses_processados = 0
    await db_session.commit()
    await exec_repo.mark_interrupted()
    await db_session.refresh(vazio)
    assert vazio.status == "error"


def test_producao_nao_expoe_docs_nem_debug_routes():
    """Em produção /docs, /redoc, /openapi.json e /debug-routes não existem."""
    import os
    import subprocess
    import sys

    code = (
        "from app.main import app;"
        "paths={r.path for r in app.routes};"
        "print(sorted(p for p in paths if p in ('/docs','/redoc','/openapi.json','/debug-routes')))"
    )
    env = {**os.environ, "APP_ENV": "production"}
    out = subprocess.run(
        [sys.executable, "-c", code],
        env=env,
        capture_output=True,
        text=True,
        cwd=str(Path(__file__).resolve().parents[1]),
    )
    assert out.stdout.strip().endswith("[]"), out.stdout + out.stderr
    env["APP_ENV"] = "development"
    out = subprocess.run(
        [sys.executable, "-c", code],
        env=env,
        capture_output=True,
        text=True,
        cwd=str(Path(__file__).resolve().parents[1]),
    )
    assert "/docs" in out.stdout

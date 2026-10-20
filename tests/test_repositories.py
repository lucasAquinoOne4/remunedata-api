"""
tests/test_repositories.py
Testes unitários para a camada de repositórios.
"""

import pytest
from sqlalchemy import select

from app.persistence.models import RemunerationCollected
from app.persistence.repositories import ExecutionRepository, RemunerationRepository


@pytest.mark.asyncio
async def test_execution_repo_get_or_create_annual(db_session):
    repo = ExecutionRepository(db_session)

    # 1. Cria novo
    annual1 = await repo.get_or_create_annual(2030)
    assert annual1.id is not None
    assert annual1.ano_exercicio == 2030

    # 2. Recupera existente (mais recente)
    annual2 = await repo.get_or_create_annual(2030)
    assert annual2.id == annual1.id


@pytest.mark.asyncio
async def test_execution_repo_update_progress(db_session):
    repo = ExecutionRepository(db_session)
    # Use a year that is definitely not in previous tests to avoid leakage
    annual = await repo.get_or_create_annual(2040)

    current_pages = annual.total_paginas_consumidas

    # Update
    await repo.update_annual_progress(annual.id, pages=1, elements=150)
    await db_session.commit()
    await db_session.refresh(annual)

    assert annual.total_paginas_consumidas == current_pages + 1
    assert annual.total_registros_coletados >= 150


@pytest.mark.asyncio
async def test_remuneration_repo_save_batch(db_session):
    repo_exec = ExecutionRepository(db_session)
    repo_rem = RemunerationRepository(db_session)

    annual = await repo_exec.get_or_create_annual(2025)
    monthly = await repo_exec.create_monthly(annual.id, "01")

    items = [
        RemunerationCollected(
            execution_id=annual.id,
            monthly_execution_id=monthly.id,
            ano_exercicio=2025,
            mes_referencia="01",
            codigo_identificacao=f"TEST_{i}",
            codigo_matricula="123",
            nome_servidor="JOAO",
            raw_payload_json=f'{{"i": {i}}}',
        )
        for i in range(5)
    ]

    await repo_rem.save_batch(items)

    # Verificação
    stmt = select(RemunerationCollected).where(
        RemunerationCollected.monthly_execution_id == monthly.id
    )
    result = await db_session.execute(stmt)
    saved = result.scalars().all()
    assert len(saved) == 5

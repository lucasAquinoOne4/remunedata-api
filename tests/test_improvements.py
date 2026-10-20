"""
tests/test_improvements.py
Verificação das melhorias e correções de bugs (3, 8, 11).
"""

import pytest

from app.core.cache import cache
from app.persistence.models import RemunerationCollected
from app.persistence.repositories import ExecutionRepository, RemunerationRepository


@pytest.mark.asyncio
async def test_search_accentuation(db_session):
    repo_exec = ExecutionRepository(db_session)
    repo_rem = RemunerationRepository(db_session)

    # Setup data
    annual = await repo_exec.get_or_create_annual(2024)
    monthly = await repo_exec.create_monthly(annual.id, "01")

    item = RemunerationCollected(
        execution_id=annual.id,
        monthly_execution_id=monthly.id,
        ano_exercicio=2024,
        mes_referencia="01",
        codigo_identificacao="ACCENT_TEST",
        codigo_matricula="M1",
        nome_servidor="JOÃO DA SILVA",
        cargo="SECRETÁRIO",
        nome_orgao="ÓRGÃO DE TESTE",
        raw_payload_json='{"fixture": 1}',
    )
    db_session.add(item)
    await db_session.commit()

    # 1. Busca "JOAO" (sem acento) deve achar "JOÃO"
    items, total = await repo_rem.search(nome="JOAO")
    assert total == 1
    assert items[0].nome_servidor == "JOÃO DA SILVA"

    # 2. Busca "joao" (lowercase) deve achar "JOÃO"
    items, total = await repo_rem.search(nome="joao")
    assert total == 1

    # 3. Busca "secretario" deve achar "SECRETÁRIO"
    items, total = await repo_rem.search(cargo="secretario")
    assert total == 1

    # 4. Busca "orgao" deve achar "ÓRGÃO"
    items, total = await repo_rem.search(orgao="orgao")
    assert total == 1


@pytest.mark.asyncio
async def test_get_distinct_filters(db_session):
    repo_exec = ExecutionRepository(db_session)
    repo_rem = RemunerationRepository(db_session)

    annual = await repo_exec.get_or_create_annual(2024)
    monthly = await repo_exec.create_monthly(annual.id, "01")

    # Add items with multiple cargos/orgaos
    items = [
        RemunerationCollected(
            execution_id=annual.id,
            monthly_execution_id=monthly.id,
            ano_exercicio=2024,
            mes_referencia="01",
            codigo_identificacao=f"ID_{i}",
            codigo_matricula=f"M_{i}",
            nome_servidor=f"S_{i}",
            cargo="CARGO_A" if i % 2 == 0 else "CARGO_B",
            nome_orgao="ORGAO_X" if i < 3 else "ORGAO_Y",
            raw_payload_json=f'{{"i": {i}}}',
        )
        for i in range(5)
    ]
    db_session.add_all(items)
    await db_session.commit()

    filters = await repo_rem.get_distinct_filters()
    assert "CARGO_A" in filters["cargos"]
    assert "CARGO_B" in filters["cargos"]
    assert "ORGAO_X" in filters["orgaos"]
    assert "ORGAO_Y" in filters["orgaos"]
    assert len(filters["cargos"]) == 2
    assert len(filters["orgaos"]) == 2


@pytest.mark.asyncio
async def test_redis_cache_logic():
    # Test directly using the app.core.cache.cache instance
    key = "test:improvement:key"
    data = {"hello": "world", "list": [1, 2, 3]}

    await cache.set(key, data, ttl=10)
    cached = await cache.get(key)

    assert cached == data

    await cache.delete(key)
    cached_after = await cache.get(key)
    assert cached_after is None

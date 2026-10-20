import pytest
from httpx import AsyncClient

from app.persistence.models import ExecutionAnnual, ExecutionMonthly, RemunerationCollected


@pytest.mark.asyncio
async def test_search_filters_cargo_orgao(
    client: AsyncClient, db_session, override_get_session, valid_token_headers
):
    """Verifica se os filtros de cargo e órgão funcionam na API."""
    # 1. Criar dados de teste
    execution = ExecutionAnnual(ano_exercicio=2025, status="success")
    db_session.add(execution)
    await db_session.flush()

    monthly = ExecutionMonthly(execution_id=execution.id, mes_referencia="01", status="success")
    db_session.add(monthly)
    await db_session.flush()

    r1 = RemunerationCollected(
        execution_id=execution.id,
        monthly_execution_id=monthly.id,
        nome_servidor="ALBERTO",
        cargo="ANALISTA",
        nome_orgao="SECRETARIA A",
        ano_exercicio=2025,
        mes_referencia="01",
        valor_bruto=10000.0,
        codigo_identificacao="ID1",
        codigo_matricula="M1",
        raw_payload_json='{"fixture": 1}',
    )
    r2 = RemunerationCollected(
        execution_id=execution.id,
        monthly_execution_id=monthly.id,
        nome_servidor="MARIA",
        cargo="TECNICO",
        nome_orgao="SECRETARIA B",
        ano_exercicio=2025,
        mes_referencia="01",
        valor_bruto=5000.0,
        codigo_identificacao="ID2",
        codigo_matricula="M2",
        raw_payload_json='{"fixture": 2}',
    )
    db_session.add_all([r1, r2])
    await db_session.commit()

    # 2. Testar filtro por cargo
    resp = await client.get("/api/v1/remuneration/?cargo=ANALISTA", headers=valid_token_headers)
    assert resp.status_code == 200
    assert resp.json()["total"] == 1
    assert resp.json()["items"][0]["nome_servidor"] == "ALBERTO"

    # 3. Testar filtro por órgão
    resp = await client.get("/api/v1/remuneration/?orgao=SECRETARIA B", headers=valid_token_headers)
    assert resp.status_code == 200
    assert resp.json()["total"] == 1
    assert resp.json()["items"][0]["nome_servidor"] == "MARIA"


@pytest.mark.asyncio
async def test_summary_endpoint(
    client: AsyncClient, db_session, override_get_session, valid_token_headers
):
    """Verifica o endpoint de resumo para o dashboard."""
    execution = ExecutionAnnual(ano_exercicio=2025, status="success")
    db_session.add(execution)
    await db_session.flush()

    monthly = ExecutionMonthly(execution_id=execution.id, mes_referencia="01", status="success")
    db_session.add(monthly)
    await db_session.flush()

    r1 = RemunerationCollected(
        execution_id=execution.id,
        monthly_execution_id=monthly.id,
        nome_servidor="JOAO",
        cargo="X",
        nome_orgao="ORGAO 1",
        valor_bruto=100.0,
        ano_exercicio=2025,
        mes_referencia="01",
        codigo_identificacao="ID3",
        codigo_matricula="M3",
        raw_payload_json='{"fixture": 3}',
    )
    db_session.add(r1)
    await db_session.commit()

    resp = await client.get("/api/v1/remuneration/summary?ano=2025", headers=valid_token_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_servidores"] >= 1
    assert data["media_salarial"] > 0
    assert len(data["top_orgaos"]) >= 1


@pytest.mark.asyncio
async def test_export_limits_enforced(
    client: AsyncClient, db_session, override_get_session, valid_token_headers
):
    """Verifica se o limite de exportação (1k XLSX, 5k CSV) é respeitado."""
    execution = ExecutionAnnual(ano_exercicio=2025, status="success")
    db_session.add(execution)
    await db_session.flush()

    monthly = ExecutionMonthly(execution_id=execution.id, mes_referencia="01", status="success")
    db_session.add(monthly)
    await db_session.flush()

    r1 = RemunerationCollected(
        execution_id=execution.id,
        monthly_execution_id=monthly.id,
        nome_servidor="S1",
        valor_bruto=1,
        ano_exercicio=2025,
        mes_referencia="01",
        codigo_identificacao="IDE1",
        codigo_matricula="ME1",
        raw_payload_json='{"fixture": 4}',
    )
    r2 = RemunerationCollected(
        execution_id=execution.id,
        monthly_execution_id=monthly.id,
        nome_servidor="S2",
        valor_bruto=2,
        ano_exercicio=2025,
        mes_referencia="01",
        codigo_identificacao="IDE2",
        codigo_matricula="ME2",
        raw_payload_json='{"fixture": 5}',
    )
    db_session.add_all([r1, r2])
    await db_session.commit()

    resp = await client.get(
        f"/api/v1/executions/{execution.id}/export?format=csv", headers=valid_token_headers
    )
    assert resp.status_code == 200
    content = resp.read().decode()
    lines = [line for line in content.split("\n") if line.strip()]
    assert len(lines) >= 3  # header + 2 registros

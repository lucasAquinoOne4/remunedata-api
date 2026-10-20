"""
tests/test_audit_fixes.py
Regressão dos achados da auditoria (squad): cada teste cobre uma correção.
"""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import respx
from sqlalchemy import func, select

from app.collector.annual import AnnualCollector
from app.collector.senado import (
    MIN_CADASTRO,
    SenadoCollector,
    build_record,
    parse_brl,
)
from app.persistence.models import RemunerationCollected, UserSubscription
from app.persistence.repositories import (
    ExecutionRepository,
    RemunerationRepository,
    SenadoRepository,
)

# ───────────── coleta do DF ─────────────


@pytest.mark.asyncio
async def test_mes_vazio_marcado_success_volta_a_ser_tentado(db_session):
    """Mês 'success' com 0 registros (portal ainda não publicou) NÃO pode ser pulado."""
    repo = ExecutionRepository(db_session)
    annual = await repo.get_or_create_annual(2094)
    vazio = await repo.create_monthly(annual.id, "12")
    vazio.status, vazio.registros_coletados = "success", 0
    cheio = await repo.create_monthly(annual.id, "11")
    cheio.status, cheio.registros_coletados = "success", 10
    await db_session.commit()

    monthly_collector = MagicMock()
    monthly_collector.collect = AsyncMock(return_value=MagicMock(status="success"))
    await AnnualCollector(monthly_collector, repo).run(2094)

    coletados = {c.kwargs["mes"] for c in monthly_collector.collect.call_args_list}
    assert "12" in coletados  # reprocessado
    assert "11" not in coletados  # concluído com dados: pulado


@pytest.mark.asyncio
async def test_save_batch_fatia_inserts_grandes(db_session):
    """Mais de 500 linhas (limite de 32767 parâmetros do asyncpg) não pode quebrar."""
    exec_repo = ExecutionRepository(db_session)
    repo = RemunerationRepository(db_session)
    annual = await exec_repo.get_or_create_annual(2093)
    monthly = await exec_repo.create_monthly(annual.id, "01")
    items = [
        RemunerationCollected(
            execution_id=annual.id,
            monthly_execution_id=monthly.id,
            ano_exercicio=2093,
            mes_referencia="01",
            codigo_identificacao=f"BIG_{i}",
            codigo_matricula="M",
            nome_servidor=f"SERVIDOR {i}",
            raw_payload_json=f'{{"i": {i}, "u": "{uuid.uuid4()}"}}',
        )
        for i in range(1200)
    ]
    await repo.save_batch(items)
    total = (
        await db_session.execute(
            select(func.count())
            .select_from(RemunerationCollected)
            .where(RemunerationCollected.ano_exercicio == 2093)
        )
    ).scalar_one()
    assert total == 1200


@pytest.mark.asyncio
async def test_429_esgotado_nao_abre_o_circuit_breaker():
    """PortalRateLimited fica fora de HTTPStatusError, então o breaker não conta como falha."""
    from circuitbreaker import CircuitBreakerMonitor

    from app.infra.transparencia_client import PortalRateLimited, TransparenciaClient

    client = TransparenciaClient()
    base = client.base_url
    with (
        respx.mock,
        patch("app.infra.transparencia_client.asyncio.sleep", new=AsyncMock()),
        patch("app.infra.transparencia_client.locks.ban_remaining", return_value=0.0),
        patch("app.infra.transparencia_client.locks.set_ban"),
    ):
        respx.get(base).mock(return_value=httpx.Response(429, headers={"Retry-After": "1"}))
        for _ in range(8):  # bem acima do threshold de 5 do breaker
            with pytest.raises(PortalRateLimited):
                await client.get_remuneracao(2026, "01", page=0)

    breakers = [b for b in CircuitBreakerMonitor.get_circuits() if "get_remuneracao" in b.name]
    assert breakers, "circuit breaker de get_remuneracao não encontrado"
    assert all(b.state == "closed" for b in breakers)


# ───────────── Senado ─────────────


def test_parse_brl_robusto():
    assert parse_brl("5.612,35") == 5612.35
    assert parse_brl("5612.35") == 5612.35  # ponto decimal NÃO vira 561235
    assert parse_brl("-1.100,00") == -1100.0
    for ruim in ("NaN", "Infinity", "-Infinity", "R$ 1.000,00", "abc", "", None):
        assert parse_brl(ruim) == 0.0
    assert parse_brl(float("nan")) == 0.0


def test_build_record_usa_ano_mes_pedidos():
    item = {"sequencial": 1, "nome": "X", "ano": 1999, "mes": 1}
    rec = build_record(item, None, ano=2026, mes=8)
    assert (rec.ano, rec.mes) == (2026, 8)
    # sem ano/mes no item não quebra
    rec2 = build_record({"sequencial": 2, "nome": "Y"}, None, ano=2026, mes=8)
    assert (rec2.ano, rec2.mes) == (2026, 8)


def _cadastro_grande(extra):
    base = [{"sequencial": 10_000 + i, "nome": f"NOME {i}"} for i in range(MIN_CADASTRO)]
    return base + extra


@pytest.mark.asyncio
async def test_homonimo_na_folha_nao_recebe_cargo_do_cadastro():
    client = AsyncMock()
    client.get_remuneracoes.return_value = [
        {"sequencial": 1, "nome": "MARIA SILVA", "tipo_folha": "Normal"},
        {"sequencial": 2, "nome": "MARIA SILVA", "tipo_folha": "Normal"},  # homônimo na folha
        {"sequencial": 3, "nome": "JOAO UNICO", "tipo_folha": "Normal"},
    ]
    client.get_servidores.return_value = _cadastro_grande(
        [
            {"sequencial": 900, "nome": "Maria Silva", "cargo": "ANALISTA"},
            {"sequencial": 901, "nome": "João Único", "cargo": "TECNICO"},
        ]
    )
    repo = AsyncMock()
    await SenadoCollector(client, repo).collect_month(2026, 8)
    records = {r.sequencial: r for r in repo.replace_month.call_args.args[2]}
    assert records[1].cargo is None and records[2].cargo is None  # ambíguos
    assert records[3].cargo == "TECNICO"  # nome único (com acento no cadastro)


@pytest.mark.asyncio
async def test_cadastro_incompleto_nao_substitui_o_mes():
    client = AsyncMock()
    client.get_remuneracoes.return_value = [{"sequencial": 1, "nome": "A", "tipo_folha": "Normal"}]
    client.get_servidores.return_value = [{"sequencial": 1, "nome": "A"}]  # só 1 registro
    repo = AsyncMock()
    with pytest.raises(RuntimeError, match="incompleto"):
        await SenadoCollector(client, repo).collect_month(2026, 8)
    repo.replace_month.assert_not_called()


@pytest.mark.asyncio
@respx.mock
async def test_senado_client_resposta_nao_lista_vira_erro():
    from app.infra.senado_client import SenadoClient

    client = SenadoClient()
    respx.get(f"{client.base_url}/servidores/servidores").mock(
        return_value=httpx.Response(200, json={"erro": "manutenção"})
    )
    with pytest.raises(ValueError, match="esperado lista"):
        await client.get_servidores()


@pytest.mark.usefixtures("override_auth")
@pytest.mark.asyncio
async def test_busca_trata_percent_e_underscore_como_texto(db_session, override_get_session):
    repo = SenadoRepository(db_session)
    await repo.replace_month(
        2092,
        3,
        [
            build_record(
                {"sequencial": 77, "nome": "JOSE ALVES", "tipo_folha": "Normal"}, None, 2092, 3
            )
        ],
    )
    _, total_pct = await repo.search(nome="%", ano=2092)
    _, total_us = await repo.search(nome="_", ano=2092)
    _, total_ok = await repo.search(nome="jose álv", ano=2092)  # sem acento, caixa e acento
    assert total_pct == 0 and total_us == 0  # antes '%' casava com tudo
    assert total_ok == 1


# ───────────── pagamento / webhook ─────────────


@pytest.mark.asyncio
async def test_webhook_sem_segredo_configurado_e_fail_closed_em_producao(client):
    with patch("app.api.endpoints.subscriptions.settings") as s:
        s.abacatepay_webhook_secret = ""
        s.app_env = "production"
        s.abacatepay_public_key = ""
        resp = await client.post(
            "/api/v1/subscriptions/webhook?webhookSecret=qualquer",
            json={"event": "checkout.completed", "data": {}},
        )
    assert resp.status_code == 401
    with patch("app.api.endpoints.subscriptions.settings") as s:
        s.abacatepay_webhook_secret = ""
        s.app_env = "development"
        s.abacatepay_public_key = ""
        resp = await client.post(
            "/api/v1/subscriptions/webhook?webhookSecret=qualquer",
            json={"event": "unknown", "data": {}},
        )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_estorno_v2_revoga_assinatura_ativa(client, db_session, override_get_session):
    from datetime import datetime, timedelta, timezone

    from app.persistence.models import SubscriptionPlan

    plan = SubscriptionPlan(
        slug=f"p-{uuid.uuid4().hex[:6]}",
        name="P",
        description="d",
        price_brl=6.99,
        duration_days=30,
        is_active=True,
    )
    db_session.add(plan)
    await db_session.commit()
    await db_session.refresh(plan)
    now = datetime.now(timezone.utc)
    sub = UserSubscription(
        user_id="user-estorno",
        plan_id=plan.id,
        abacatepay_billing_id="bill_estorno_ativo",
        status="active",
        starts_at=now,
        expires_at=now + timedelta(days=30),
    )
    db_session.add(sub)
    await db_session.commit()
    await db_session.refresh(sub)

    resp = await client.post(
        "/api/v1/subscriptions/webhook?webhookSecret=",
        json={"event": "checkout.refunded", "data": {"checkout": {"id": "bill_estorno_ativo"}}},
    )
    assert resp.status_code == 200
    await db_session.refresh(sub)
    assert sub.status == "canceled"
    assert sub.expires_at <= datetime.now(timezone.utc) + timedelta(seconds=5)


def test_admin_padrao_vazio_e_summary_valida_ano():
    from app.core.config import Settings

    # sem ADMIN_EMAILS, ninguém é admin (antes valia o e-mail do domínio do dono)
    assert Settings.model_fields["admin_emails"].default == []

    import inspect

    from app.api.endpoints.remuneration import get_summary

    ano = inspect.signature(get_summary).parameters["ano"].default
    assert {type(m).__name__ for m in ano.metadata} >= {"Ge", "Le"}

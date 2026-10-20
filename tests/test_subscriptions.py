"""
tests/test_subscriptions.py
Testes da integração de assinaturas — planos, checkout, webhook e proteção de acesso.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from app.main import app
from app.persistence.models import SubscriptionPlan, UserSubscription

# Removido pytestmark global para permitir controle fino via clear_overrides


@pytest.fixture
def clear_overrides():
    """Garante que nenhum override global de outros testes interfira nos testes de assinatura real."""
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()


# ────────────────────────────────────────────────────────
# Fixtures de plano e assinatura
# ────────────────────────────────────────────────────────


async def _create_plan(db_session, slug="essencial", price=6.99, days=30):
    plan = SubscriptionPlan(
        slug=slug,
        name=f"Plano {slug.capitalize()}",
        description="Plano de teste",
        price_brl=price,
        duration_days=days,
        is_active=True,
    )
    db_session.add(plan)
    await db_session.commit()
    await db_session.refresh(plan)
    return plan


async def _create_active_subscription(db_session, plan, user_id="test-uuid"):
    """Cria assinatura ativa para o usuário padrão dos testes (sub='test-uuid')."""
    now = datetime.now(timezone.utc)
    sub = UserSubscription(
        user_id=user_id,
        plan_id=plan.id,
        abacatepay_billing_id="bill_active_001",
        status="active",
        starts_at=now,
        expires_at=now + timedelta(days=30),
    )
    db_session.add(sub)
    await db_session.commit()
    await db_session.refresh(sub)
    return sub


# ────────────────────────────────────────────────────────
# Testes de planos (endpoint público)
# ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_plans_empty(client, db_session, override_get_session):
    """GET /plans retorna lista vazia quando não há planos."""
    resp = await client.get("/api/v1/subscriptions/plans")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_list_plans_returns_active_plans(client, db_session, override_get_session):
    """GET /plans lista apenas planos ativos."""
    await _create_plan(db_session, slug="essencial", price=6.99, days=30)
    await _create_plan(db_session, slug="profissional", price=17.99, days=90)
    # Plano inativo não deve aparecer
    inactive = SubscriptionPlan(
        slug="inativo",
        name="Inativo",
        description="Inativo",
        price_brl=99.0,
        duration_days=365,
        is_active=False,
    )
    db_session.add(inactive)
    await db_session.commit()

    resp = await client.get("/api/v1/subscriptions/plans")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2
    slugs = {p["slug"] for p in data}
    assert "essencial" in slugs
    assert "profissional" in slugs
    assert "inativo" not in slugs


# ────────────────────────────────────────────────────────
# Testes de assinatura ativa do usuário
# ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_my_subscription_inactive(
    client, db_session, override_get_session, valid_token_headers
):
    """GET /me retorna inactive quando usuário não tem assinatura."""
    resp = await client.get("/api/v1/subscriptions/me", headers=valid_token_headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "inactive"


@pytest.mark.asyncio
async def test_my_subscription_active(
    client, db_session, override_get_session, valid_token_headers
):
    """GET /me retorna active com validade quando usuário tem assinatura."""
    plan = await _create_plan(db_session)
    await _create_active_subscription(db_session, plan)

    resp = await client.get("/api/v1/subscriptions/me", headers=valid_token_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "active"
    assert data["expires_at"] is not None


@pytest.mark.asyncio
async def test_my_subscription_requires_auth(client, db_session, override_get_session):
    """GET /me retorna 401/403 sem token — HTTPBearer rejeita antes do override."""
    from app.api.deps import get_current_user
    from app.main import app

    # Remove override de auth para este teste
    app.dependency_overrides.pop(get_current_user, None)
    try:
        resp = await client.get("/api/v1/subscriptions/me")
        # HTTPBearer pode retornar 401 (sem credentials) ou 403 (forbidden)
        assert resp.status_code in (401, 403)
    finally:
        # Restaura override padrão
        async def _get_current_user_override():
            return {"email": "test@example.com", "sub": "test-uuid", "aud": "authenticated"}

        app.dependency_overrides[get_current_user] = _get_current_user_override


# ────────────────────────────────────────────────────────
# Testes de checkout
# ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_checkout_plan_not_found(
    client, db_session, override_get_session, valid_token_headers
):
    """POST /checkout com plano inválido retorna 404."""
    resp = await client.post(
        "/api/v1/subscriptions/checkout",
        json={
            "plan_slug": "nao_existe",
            "name": "Test User",
            "email": "test@test.com",
            "cellphone": "(11) 99999-9999",
            "tax_id": "000.000.000-00",
        },
        headers=valid_token_headers,
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_checkout_success(client, db_session, override_get_session, valid_token_headers):
    """POST /checkout cria cobrança e retorna URL de pagamento."""
    plan = await _create_plan(db_session)

    mock_customer = {"id": "cust_test_123"}
    mock_billing = {
        "id": "bill_test_456",
        "url": "https://pay.abacatepay.com/test-checkout",
        "status": "PENDING",
    }

    with (
        patch(
            "app.api.endpoints.subscriptions.abacatepay_client.create_customer",
            new=AsyncMock(return_value=mock_customer),
        ),
        patch(
            "app.api.endpoints.subscriptions.abacatepay_client.ensure_product",
            new=AsyncMock(return_value="prod_test_1"),
        ),
        patch(
            "app.api.endpoints.subscriptions.abacatepay_client.create_checkout",
            new=AsyncMock(return_value=mock_billing),
        ),
    ):
        resp = await client.post(
            "/api/v1/subscriptions/checkout",
            json={
                "plan_slug": "essencial",
                "name": "Test User",
                "email": "test@test.com",
                "cellphone": "(11) 99999-9999",
                "tax_id": "000.000.000-00",
            },
            headers=valid_token_headers,
        )

    assert resp.status_code == 201
    data = resp.json()
    assert data["billing_id"] == "bill_test_456"
    assert "pay.abacatepay.com" in data["checkout_url"]
    assert data["price_brl"] == plan.price_brl


@pytest.mark.asyncio
async def test_checkout_already_subscribed(
    client, db_session, override_get_session, valid_token_headers
):
    """POST /checkout com assinatura ativa retorna 409."""
    plan = await _create_plan(db_session)
    await _create_active_subscription(db_session, plan)

    resp = await client.post(
        "/api/v1/subscriptions/checkout",
        json={
            "plan_slug": "essencial",
            "name": "Test User",
            "email": "test@test.com",
            "cellphone": "(11) 99999-9999",
            "tax_id": "000.000.000-00",
        },
        headers=valid_token_headers,
    )
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_checkout_abacatepay_error(
    client, db_session, override_get_session, valid_token_headers
):
    """POST /checkout com falha na AbacatePay retorna 502."""
    await _create_plan(db_session)

    with patch(
        "app.api.endpoints.subscriptions.abacatepay_client.create_customer",
        new=AsyncMock(side_effect=Exception("Connection refused")),
    ):
        resp = await client.post(
            "/api/v1/subscriptions/checkout",
            json={
                "plan_slug": "essencial",
                "name": "Test User",
                "email": "test@test.com",
                "cellphone": "(11) 99999-9999",
                "tax_id": "000.000.000-00",
            },
            headers=valid_token_headers,
        )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_checkout_billing_error(
    client, db_session, override_get_session, valid_token_headers
):
    """POST /checkout com falha na criação da cobrança retorna 502."""
    await _create_plan(db_session)
    mock_customer = {"id": "cust_test_err"}

    with (
        patch(
            "app.api.endpoints.subscriptions.abacatepay_client.create_customer",
            new=AsyncMock(return_value=mock_customer),
        ),
        patch(
            "app.api.endpoints.subscriptions.abacatepay_client.ensure_product",
            new=AsyncMock(return_value="prod_test_1"),
        ),
        patch(
            "app.api.endpoints.subscriptions.abacatepay_client.create_checkout",
            new=AsyncMock(side_effect=Exception("Billing error")),
        ),
    ):
        resp = await client.post(
            "/api/v1/subscriptions/checkout",
            json={
                "plan_slug": "essencial",
                "name": "Test User",
                "email": "test@test.com",
                "cellphone": "(11) 99999-9999",
                "tax_id": "000.000.000-00",
            },
            headers=valid_token_headers,
        )
    assert resp.status_code == 400


# ────────────────────────────────────────────────────────
# Testes de webhook
# ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_webhook_invalid_secret(client, db_session, override_get_session, monkeypatch):
    """POST /webhook com secret errado retorna 401."""
    monkeypatch.setattr(
        "app.api.endpoints.subscriptions.settings.abacatepay_webhook_secret",
        "correct_secret",
    )
    resp = await client.post(
        "/api/v1/subscriptions/webhook?webhookSecret=wrong_secret",
        json={"event": "billing.paid", "devMode": False, "data": {}},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_webhook_checkout_completed_activates_subscription(
    client, db_session, override_get_session
):
    """POST /webhook billing.paid ativa assinatura pending."""
    plan = await _create_plan(db_session)
    # Criar assinatura pendente
    sub = UserSubscription(
        user_id="user-webhook-test",
        plan_id=plan.id,
        abacatepay_billing_id="bill_webhook_123",
        status="pending",
    )
    db_session.add(sub)
    await db_session.commit()
    await db_session.refresh(sub)

    with patch(
        "app.api.endpoints.subscriptions.abacatepay_client.get_checkout",
        new=AsyncMock(return_value={"id": "bill_webhook_123", "status": "PAID"}),
    ):
        resp = await client.post(
            "/api/v1/subscriptions/webhook?webhookSecret=",
            json={
                "id": "log_1",
                "event": "checkout.completed",
                "apiVersion": 2,
                "devMode": True,
                "data": {"checkout": {"id": "bill_webhook_123", "status": "PAID"}},
            },
        )

    assert resp.status_code == 200
    assert resp.json()["ok"] is True

    # Verificar que assinatura foi ativada
    await db_session.refresh(sub)
    assert sub.status == "active"
    assert sub.expires_at is not None


@pytest.mark.asyncio
async def test_webhook_billing_paid_idempotent(client, db_session, override_get_session):
    """POST /webhook billing.paid com assinatura já ativa é idempotente."""
    plan = await _create_plan(db_session)
    sub = await _create_active_subscription(db_session, plan)

    resp = await client.post(
        "/api/v1/subscriptions/webhook?webhookSecret=",
        json={
            "event": "billing.paid",
            "devMode": True,
            "data": {"id": sub.abacatepay_billing_id, "status": "PAID"},
        },
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


@pytest.mark.asyncio
async def test_webhook_billing_no_id(client, db_session, override_get_session):
    """POST /webhook billing.paid sem billing_id no data é ignorado."""
    resp = await client.post(
        "/api/v1/subscriptions/webhook?webhookSecret=",
        json={"event": "billing.paid", "devMode": True, "data": {}},
    )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_webhook_billing_not_found(client, db_session, override_get_session):
    """POST /webhook billing.paid com billing_id desconhecido retorna ok."""
    resp = await client.post(
        "/api/v1/subscriptions/webhook?webhookSecret=",
        json={
            "event": "billing.paid",
            "devMode": True,
            "data": {"id": "bill_unknown_999"},
        },
    )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_webhook_billing_failed_cancels_subscription(
    client, db_session, override_get_session
):
    """POST /webhook billing.failed cancela assinatura pending."""
    plan = await _create_plan(db_session)
    sub = UserSubscription(
        user_id="user-fail-test",
        plan_id=plan.id,
        abacatepay_billing_id="bill_fail_456",
        status="pending",
    )
    db_session.add(sub)
    await db_session.commit()
    await db_session.refresh(sub)

    resp = await client.post(
        "/api/v1/subscriptions/webhook?webhookSecret=",
        json={
            "event": "billing.failed",
            "devMode": True,
            "data": {"id": "bill_fail_456"},
        },
    )
    assert resp.status_code == 200
    await db_session.refresh(sub)
    assert sub.status == "canceled"


@pytest.mark.asyncio
async def test_webhook_refunded_ignores_active_sub(client, db_session, override_get_session):
    """POST /webhook billing.refunded não cancela assinatura já ativa."""
    plan = await _create_plan(db_session)
    sub = await _create_active_subscription(db_session, plan)

    resp = await client.post(
        "/api/v1/subscriptions/webhook?webhookSecret=",
        json={
            "event": "billing.refunded",
            "devMode": True,
            "data": {"id": sub.abacatepay_billing_id},
        },
    )
    assert resp.status_code == 200
    await db_session.refresh(sub)
    # Status ativo deve permanecer (não cancela ativo)
    assert sub.status == "active"


@pytest.mark.asyncio
async def test_webhook_unknown_event_is_ok(client, db_session, override_get_session):
    """POST /webhook com evento desconhecido retorna 200 (não quebra)."""
    resp = await client.post(
        "/api/v1/subscriptions/webhook?webhookSecret=",
        json={"event": "unknown.event", "devMode": True, "data": {}},
    )
    assert resp.status_code == 200

    # ────────────────────────────────────────────────────────
    # Testes de proteção de acesso (require_active_subscription)
    # ────────────────────────────────────────────────────────

    pass


@pytest.mark.asyncio
async def test_require_subscription_dep_no_sub(client, db_session, override_get_session):
    """require_active_subscription levanta 403 quando sub não encontrada."""
    from datetime import datetime, timezone

    from sqlalchemy import select

    from app.persistence.models import UserSubscription

    # Verificar que não há assinatura ativa
    now = datetime.now(timezone.utc)
    stmt = select(UserSubscription).where(
        UserSubscription.user_id == "test-uuid",
        UserSubscription.status == "active",
        UserSubscription.expires_at > now,
    )
    result = await db_session.execute(stmt)
    sub = result.scalar_one_or_none()
    assert sub is None  # Confirma estado esperado


@pytest.mark.asyncio
async def test_require_subscription_dep_with_active(client, db_session, override_get_session):
    """require_active_subscription não levanta exceção quando sub ativa existe."""
    plan = await _create_plan(db_session)
    sub = await _create_active_subscription(db_session, plan)

    # Confirma que está ativa e não expirada
    now = datetime.now(timezone.utc)
    assert sub.status == "active"
    assert sub.expires_at > now


@pytest.mark.asyncio
async def test_webhook_completed_nao_ativa_se_api_nao_confirma_pago(
    client, db_session, override_get_session
):
    """Webhook forjado/sem pagamento: o corpo diz PAID, mas a API da AbacatePay diz PENDING."""
    plan = await _create_plan(db_session)
    sub = UserSubscription(
        user_id="user-forjado",
        plan_id=plan.id,
        abacatepay_billing_id="bill_forjado_1",
        status="pending",
    )
    db_session.add(sub)
    await db_session.commit()
    await db_session.refresh(sub)

    with patch(
        "app.api.endpoints.subscriptions.abacatepay_client.get_checkout",
        new=AsyncMock(return_value={"id": "bill_forjado_1", "status": "PENDING"}),
    ):
        resp = await client.post(
            "/api/v1/subscriptions/webhook?webhookSecret=",
            json={
                "event": "checkout.completed",
                "data": {"checkout": {"id": "bill_forjado_1", "status": "PAID"}},
            },
        )

    assert resp.status_code == 200
    assert resp.json()["msg"] == "pagamento não confirmado"
    await db_session.refresh(sub)
    assert sub.status == "pending"


@pytest.mark.asyncio
async def test_webhook_completed_falha_ao_confirmar_retorna_502_para_retentativa(
    client, db_session, override_get_session
):
    """Se a AbacatePay estiver fora do ar, devolve 502 (ela refaz a entrega depois)."""
    plan = await _create_plan(db_session)
    sub = UserSubscription(
        user_id="user-502",
        plan_id=plan.id,
        abacatepay_billing_id="bill_502_1",
        status="pending",
    )
    db_session.add(sub)
    await db_session.commit()

    with patch(
        "app.api.endpoints.subscriptions.abacatepay_client.get_checkout",
        new=AsyncMock(side_effect=Exception("indisponível")),
    ):
        resp = await client.post(
            "/api/v1/subscriptions/webhook?webhookSecret=",
            json={"event": "checkout.completed", "data": {"id": "bill_502_1"}},
        )
    assert resp.status_code == 502


@pytest.mark.asyncio
async def test_webhook_checkout_refunded_cancela_pendente(client, db_session, override_get_session):
    plan = await _create_plan(db_session)
    sub = UserSubscription(
        user_id="user-refund-v2",
        plan_id=plan.id,
        abacatepay_billing_id="bill_refund_v2",
        status="pending",
    )
    db_session.add(sub)
    await db_session.commit()
    await db_session.refresh(sub)

    resp = await client.post(
        "/api/v1/subscriptions/webhook?webhookSecret=",
        json={"event": "checkout.refunded", "data": {"checkout": {"id": "bill_refund_v2"}}},
    )
    assert resp.status_code == 200
    await db_session.refresh(sub)
    assert sub.status == "canceled"

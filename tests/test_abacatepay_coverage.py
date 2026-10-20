"""
tests/test_abacatepay_coverage.py
Testes do cliente AbacatePay (API v2) e dos helpers de assinatura de webhook.
"""

import base64
import hashlib
import hmac
from unittest.mock import patch

import httpx
import pytest
import respx

from app.infra.abacatepay_client import AbacatePayClient

# ────────────────────────────────────────────────────────
# Testes do AbacatePayClient (API v2)
# ────────────────────────────────────────────────────────

BASE = "https://api.abacatepay.com/v2"


def _client() -> AbacatePayClient:
    c = AbacatePayClient()
    c._headers["Authorization"] = "Bearer abc_dev_test"
    return c


@pytest.mark.asyncio
@respx.mock
async def test_create_customer_success():
    route = respx.post(f"{BASE}/customers/create").mock(
        return_value=httpx.Response(200, json={"data": {"id": "cust_abc123"}, "error": None})
    )
    result = await _client().create_customer(
        "Test User", "t@t.com", "+5511999999999", "11144477735"
    )
    assert result["id"] == "cust_abc123"
    sent = route.calls.last.request
    assert sent.headers["authorization"] == "Bearer abc_dev_test"
    assert b'"taxId"' in sent.content


@pytest.mark.asyncio
@respx.mock
async def test_error_field_raises_value_error():
    respx.post(f"{BASE}/customers/create").mock(
        return_value=httpx.Response(200, json={"data": None, "error": "invalid_tax_id"})
    )
    with pytest.raises(ValueError, match="AbacatePay error: invalid_tax_id"):
        await _client().create_customer("A", "b@b.com", "1", "x")


@pytest.mark.asyncio
@respx.mock
async def test_http_error_propagates():
    respx.post(f"{BASE}/customers/create").mock(
        return_value=httpx.Response(
            401, json={"success": False, "error": "API key version mismatch"}
        )
    )
    with pytest.raises(httpx.HTTPStatusError):
        await _client().create_customer("A", "b@b.com", "1", "x")


@pytest.mark.asyncio
@respx.mock
async def test_ensure_product_reaproveita_existente_e_usa_cache():
    ext = AbacatePayClient.product_external_id("essencial", 699)
    assert ext == "remunedata-essencial-699"
    listing = respx.get(f"{BASE}/products/list").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [
                    {"id": "prod_outro", "externalId": "x"},
                    {"id": "prod_ok", "externalId": ext},
                ],
                "pagination": {"hasMore": False, "next": None},
            },
        )
    )
    create = respx.post(f"{BASE}/products/create")
    client = _client()
    assert await client.ensure_product("essencial", "Plano", "desc", 699) == "prod_ok"
    assert await client.ensure_product("essencial", "Plano", "desc", 699) == "prod_ok"
    assert listing.call_count == 1  # segunda chamada vem do cache
    assert not create.called


@pytest.mark.asyncio
@respx.mock
async def test_ensure_product_cria_quando_nao_existe_e_segue_paginacao():
    respx.get(f"{BASE}/products/list").mock(
        side_effect=[
            httpx.Response(200, json={"data": [], "pagination": {"hasMore": True, "next": "c2"}}),
            httpx.Response(200, json={"data": [], "pagination": {"hasMore": False, "next": None}}),
        ]
    )
    create = respx.post(f"{BASE}/products/create").mock(
        return_value=httpx.Response(200, json={"data": {"id": "prod_novo"}, "error": None})
    )
    assert await _client().ensure_product("anual", "Plano Anual", "d", 4999) == "prod_novo"
    body = create.calls.last.request.content
    assert b'"price":4999' in body.replace(b" ", b"") and b"BRL" in body


@pytest.mark.asyncio
@respx.mock
async def test_ensure_product_corrida_de_criacao():
    ext = AbacatePayClient.product_external_id("profissional", 1799)
    respx.get(f"{BASE}/products/list").mock(
        side_effect=[
            httpx.Response(200, json={"data": [], "pagination": {"hasMore": False}}),
            httpx.Response(
                200,
                json={
                    "data": [{"id": "prod_corrida", "externalId": ext}],
                    "pagination": {"hasMore": False},
                },
            ),
        ]
    )
    respx.post(f"{BASE}/products/create").mock(
        return_value=httpx.Response(
            400, json={"success": False, "error": "Product with externalId already exists"}
        )
    )
    assert await _client().ensure_product("profissional", "P", "d", 1799) == "prod_corrida"


@pytest.mark.asyncio
@respx.mock
async def test_create_checkout_payload_v2():
    route = respx.post(f"{BASE}/checkouts/create").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {
                    "id": "bill_xyz",
                    "url": "https://app.abacatepay.com/pay/bill_xyz",
                    "status": "PENDING",
                },
                "error": None,
            },
        )
    )
    result = await _client().create_checkout("cust_1", "prod_1", "remunedata-ext-1", "essencial")
    assert result["id"] == "bill_xyz"
    import json

    sent = json.loads(route.calls.last.request.content)
    assert sent["items"] == [{"id": "prod_1", "quantity": 1}]
    assert sent["methods"] == ["PIX", "CARD"]
    assert sent["customerId"] == "cust_1" and sent["externalId"] == "remunedata-ext-1"
    assert sent["completionUrl"].endswith("/assinatura/sucesso?billing=remunedata-ext-1")
    assert "frequency" not in sent  # não existe na v2


@pytest.mark.asyncio
@respx.mock
async def test_create_checkout_sem_cartao_na_loja_cai_para_pix():
    route = respx.post(f"{BASE}/checkouts/create").mock(
        side_effect=[
            httpx.Response(
                400,
                json={
                    "success": False,
                    "data": None,
                    "error": "CARD is not available for this store",
                },
            ),
            httpx.Response(
                200, json={"data": {"id": "bill_pix", "status": "PENDING"}, "error": None}
            ),
        ]
    )
    result = await _client().create_checkout("cust_1", "prod_1", "ext-2", "essencial")
    assert result["id"] == "bill_pix"
    import json

    assert json.loads(route.calls[0].request.content)["methods"] == ["PIX", "CARD"]
    assert json.loads(route.calls[1].request.content)["methods"] == ["PIX"]


@pytest.mark.asyncio
@respx.mock
async def test_create_checkout_outros_erros_400_nao_sao_engolidos():
    respx.post(f"{BASE}/checkouts/create").mock(
        return_value=httpx.Response(400, json={"success": False, "error": "customer not found"})
    )
    with pytest.raises(httpx.HTTPStatusError):
        await _client().create_checkout("cust_x", "prod_1", "ext-3", "essencial")


@pytest.mark.asyncio
@respx.mock
async def test_get_checkout():
    route = respx.get(f"{BASE}/checkouts/get").mock(
        return_value=httpx.Response(
            200, json={"data": {"id": "bill_xyz", "status": "PAID"}, "error": None}
        )
    )
    result = await _client().get_checkout("bill_xyz")
    assert result["status"] == "PAID"
    assert route.calls.last.request.url.params["id"] == "bill_xyz"


# ────────────────────────────────────────────────────────
# Testes dos helpers de webhook (verify_webhook_secret, verify_hmac)
# ────────────────────────────────────────────────────────


def test_verify_webhook_secret_empty_secret_passes():
    """Secret vazio (dev mode) aceita qualquer valor."""
    from app.api.endpoints.subscriptions import _verify_webhook_secret

    with patch("app.api.endpoints.subscriptions.settings") as mock_settings:
        mock_settings.abacatepay_webhook_secret = ""
        assert _verify_webhook_secret("qualquer") is True


def test_verify_webhook_secret_correct():
    """Secret correto é aceito."""
    from app.api.endpoints.subscriptions import _verify_webhook_secret

    with patch("app.api.endpoints.subscriptions.settings") as mock_settings:
        mock_settings.abacatepay_webhook_secret = "my_secret"
        assert _verify_webhook_secret("my_secret") is True


def test_verify_webhook_secret_wrong():
    """Secret errado é rejeitado."""
    from app.api.endpoints.subscriptions import _verify_webhook_secret

    with patch("app.api.endpoints.subscriptions.settings") as mock_settings:
        mock_settings.abacatepay_webhook_secret = "my_secret"
        assert _verify_webhook_secret("wrong_secret") is False


def test_verify_signature_v2_sem_chave_ou_sem_header_passa():
    from app.api.endpoints.subscriptions import _verify_signature

    with patch("app.api.endpoints.subscriptions.settings") as mock_settings:
        mock_settings.abacatepay_public_key = ""
        assert _verify_signature(b"body", "qualquer") is True
        mock_settings.abacatepay_public_key = "k"
        assert _verify_signature(b"body", "") is True


def test_verify_signature_v2_valida_base64_e_rejeita_invalida():
    from app.api.endpoints.subscriptions import _verify_signature

    key = "chave-publica-teste"
    body = b'{"event":"checkout.completed"}'
    good = base64.b64encode(hmac.new(key.encode(), body, hashlib.sha256).digest()).decode()
    with patch("app.api.endpoints.subscriptions.settings") as mock_settings:
        mock_settings.abacatepay_public_key = key
        assert _verify_signature(body, good) is True
        assert _verify_signature(body, "invalida") is False
        assert _verify_signature(b"outro corpo", good) is False


def test_checkout_id_candidates_formatos_variados():
    from app.api.endpoints.subscriptions import _checkout_id_candidates

    assert _checkout_id_candidates({"data": {"checkout": {"id": "bill_1"}}}) == ["bill_1"]
    assert _checkout_id_candidates({"data": {"id": "bill_2", "customer": None}}) == ["bill_2"]
    assert "bill_3" in _checkout_id_candidates({"data": {"items": [{"x": "bill_3"}]}})
    assert _checkout_id_candidates({"data": {"checkoutId": "abc"}}) == ["abc"]
    assert _checkout_id_candidates({"data": {}}) == []


# ────────────────────────────────────────────────────────
# Teste do webhook com HMAC válido
# ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_webhook_with_valid_signature(client, db_session, override_get_session):
    """POST /webhook com X-Webhook-Signature (base64) válida passa."""
    secret, key = "test_secret", "chave-publica-teste"
    body = b'{"event":"unknown.event","devMode":true,"data":{}}'
    sig = base64.b64encode(hmac.new(key.encode(), body, hashlib.sha256).digest()).decode()

    with patch("app.api.endpoints.subscriptions.settings") as mock_settings:
        mock_settings.abacatepay_webhook_secret = secret
        mock_settings.abacatepay_public_key = key
        resp = await client.post(
            f"/api/v1/subscriptions/webhook?webhookSecret={secret}",
            content=body,
            headers={"Content-Type": "application/json", "X-Webhook-Signature": sig},
        )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_webhook_with_invalid_signature(client, db_session, override_get_session):
    """POST /webhook com X-Webhook-Signature inválida retorna 401."""
    secret, key = "test_secret", "chave-publica-teste"
    body = b'{"event":"checkout.completed","devMode":false,"data":{}}'

    with patch("app.api.endpoints.subscriptions.settings") as mock_settings:
        mock_settings.abacatepay_webhook_secret = secret
        mock_settings.abacatepay_public_key = key
        resp = await client.post(
            f"/api/v1/subscriptions/webhook?webhookSecret={secret}",
            content=body,
            headers={"Content-Type": "application/json", "X-Webhook-Signature": "invalida"},
        )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_webhook_invalid_json_payload(client, db_session, override_get_session):
    """POST /webhook com payload não-JSON retorna 400."""
    resp = await client.post(
        "/api/v1/subscriptions/webhook?webhookSecret=",
        content=b"not json at all!!",
        headers={"Content-Type": "text/plain"},
    )
    assert resp.status_code == 400


# ────────────────────────────────────────────────────────
# Teste de require_active_subscription levantando 403
# ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_require_active_subscription_raises_403(db_session, override_get_session):
    """require_active_subscription levanta HTTPException 403 sem assinatura ativa."""
    from fastapi import HTTPException

    from app.api.deps import require_active_subscription

    # Simular usuário sem assinatura
    user = {"sub": "user-sem-assinatura", "email": "no@sub.com"}

    with pytest.raises(HTTPException) as exc_info:
        await require_active_subscription(user=user, session=db_session)

    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_require_active_subscription_passes_with_sub(db_session, override_get_session):
    """require_active_subscription retorna user se assinatura ativa existe."""
    from datetime import datetime, timedelta, timezone

    from app.api.deps import require_active_subscription
    from app.persistence.models import SubscriptionPlan, UserSubscription

    # Criar plano e assinatura ativa
    plan = SubscriptionPlan(
        slug="dep-test",
        name="Dep Test",
        description="Test",
        price_brl=6.99,
        duration_days=30,
        is_active=True,
    )
    db_session.add(plan)
    await db_session.commit()
    await db_session.refresh(plan)

    now = datetime.now(timezone.utc)
    sub = UserSubscription(
        user_id="user-com-assinatura",
        plan_id=plan.id,
        abacatepay_billing_id="bill_dep_test_001",
        status="active",
        starts_at=now,
        expires_at=now + timedelta(days=30),
    )
    db_session.add(sub)
    await db_session.commit()

    user = {"sub": "user-com-assinatura", "email": "with@sub.com"}
    result = await require_active_subscription(user=user, session=db_session)
    assert result == user


@pytest.mark.asyncio
async def test_require_active_subscription_bypass_for_admin(db_session, override_get_session):
    """require_active_subscription libera acesso total para e-mails na whitelist admin."""
    from app.api.deps import require_active_subscription
    from app.core.config import settings

    # 1. Pega um e-mail da lista de admin
    admin_email = settings.admin_emails[0]
    user = {"sub": "admin-id", "email": admin_email}

    # 2. Chama sem nem precisar de assinatura no banco
    result = await require_active_subscription(user=user, session=db_session)
    assert result == user

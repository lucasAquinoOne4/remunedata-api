"""
app/api/endpoints/subscriptions.py
Endpoints para gerenciamento de assinaturas via AbacatePay.
"""

import base64
import hashlib
import hmac
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_admin_repository, get_current_user, get_session
from app.core.config import settings
from app.infra.abacatepay_client import abacatepay_client
from app.persistence.admin_repository import AdminRepository
from app.persistence.models import SubscriptionPlan, UserSubscription

logger = logging.getLogger(__name__)
router = APIRouter()


# ─── Schemas ────────────────────────────────────────────────────────────────


class PlanOut(BaseModel):
    slug: str
    name: str
    description: str
    price_brl: float
    duration_days: int

    model_config = {"from_attributes": True}


class CheckoutRequest(BaseModel):
    plan_slug: str
    name: str
    email: str
    cellphone: str
    tax_id: str  # CPF ou CNPJ


class CheckoutResponse(BaseModel):
    billing_id: str
    checkout_url: str
    plan: str
    price_brl: float


class SubscriptionOut(BaseModel):
    status: str
    plan: Optional[str] = None
    expires_at: Optional[datetime] = None


# ─── Helpers ─────────────────────────────────────────────────────────────────


async def _get_active_subscription(
    user_id: str,
    session: AsyncSession,
) -> Optional[UserSubscription]:
    """Retorna a assinatura ativa mais recente do usuário, ou None."""
    now = datetime.now(timezone.utc)
    stmt = (
        select(UserSubscription)
        .where(
            UserSubscription.user_id == user_id,
            UserSubscription.status == "active",
            UserSubscription.expires_at > now,
        )
        .order_by(UserSubscription.expires_at.desc())
        .limit(1)
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


def _verify_webhook_secret(provided: str) -> bool:
    """Valida o webhookSecret na query string de forma segura (constant-time)."""
    expected = settings.abacatepay_webhook_secret
    if not expected:
        # Sem segredo configurado: aceita só fora de produção (dev/testes). Em produção é
        # fail-closed, senão qualquer um poderia chamar o webhook.
        return settings.app_env.lower() not in ("production", "prod")
    return hmac.compare_digest(str(provided), str(expected))


# ─── Endpoints ───────────────────────────────────────────────────────────────


@router.get(
    "/plans", response_model=list[PlanOut], summary="Lista planos de assinatura disponíveis"
)
async def list_plans(session: AsyncSession = Depends(get_session)):
    """Retorna todos os planos de assinatura ativos. Endpoint público."""
    stmt = (
        select(SubscriptionPlan)
        .where(SubscriptionPlan.is_active.is_(True))
        .order_by(SubscriptionPlan.price_brl)
    )
    result = await session.execute(stmt)
    plans = result.scalars().all()
    return [PlanOut.model_validate(p) for p in plans]


@router.post(
    "/checkout",
    response_model=CheckoutResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Inicia checkout de assinatura",
)
async def create_checkout(
    body: CheckoutRequest,
    user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """
    Cria uma cobrança AbacatePay e retorna a URL de pagamento.
    Requer autenticação JWT (Supabase).
    """
    # Buscar plano
    stmt = select(SubscriptionPlan).where(
        SubscriptionPlan.slug == body.plan_slug,
        SubscriptionPlan.is_active.is_(True),
    )
    result = await session.execute(stmt)
    plan = result.scalar_one_or_none()
    if not plan:
        raise HTTPException(status_code=404, detail=f"Plano '{body.plan_slug}' não encontrado.")

    user_id: str = user.get("sub", "")

    # Verificar se já tem assinatura ativa
    active = await _get_active_subscription(user_id, session)
    if active:
        raise HTTPException(
            status_code=409,
            detail="Você já possui uma assinatura ativa. Aguarde o vencimento para renovar.",
        )

    # Criar cliente na AbacatePay
    try:
        customer = await abacatepay_client.create_customer(
            name=body.name,
            email=body.email,
            cellphone=body.cellphone,
            tax_id=body.tax_id,
        )
        customer_id: str = customer["id"]
    except Exception as e:
        logger.error(f"Erro ao criar cliente AbacatePay: {e}")
        raise HTTPException(
            status_code=400,
            detail="Erro ao conectar com o gateway de pagamento ou dados invÃ¡lidos.",
        )

    # Gerar external_id único (user_id + plan + timestamp)
    ts = int(datetime.now(timezone.utc).timestamp())
    external_id = f"remunedata-{user_id[:8]}-{plan.slug}-{ts}"

    # Criar cobranÃ§a
    try:
        product_id = await abacatepay_client.ensure_product(
            plan_slug=plan.slug,
            plan_name=plan.name,
            plan_description=plan.description,
            price_cents=int(round(plan.price_brl * 100)),
        )
        billing = await abacatepay_client.create_checkout(
            customer_id=customer_id,
            product_id=product_id,
            external_id=external_id,
            plan_slug=plan.slug,
        )
    except Exception as e:
        import httpx

        detail_msg = "Erro ao gerar cobranÃ§a de pagamento."
        if isinstance(e, httpx.HTTPStatusError):
            try:
                error_body = e.response.json()
                # Tenta extrair a mensagem de erro da API do AbacatePay
                if isinstance(error_body, dict) and "error" in error_body:
                    detail_msg = f"Gateway Error: {error_body['error']}"
                elif isinstance(error_body, list) and len(error_body) > 0:
                    detail_msg = f"Erro de ValidaÃ§Ã£o: {error_body[0]}"
            except Exception:
                pass

        logger.error(f"Falha no checkout AbacatePay: {e}")
        raise HTTPException(status_code=400, detail=detail_msg)

    # Registrar assinatura pendente no banco
    subscription = UserSubscription(
        user_id=user_id,
        plan_id=plan.id,
        abacatepay_billing_id=billing["id"],
        abacatepay_customer_id=customer_id,
        status="pending",
    )
    session.add(subscription)
    await session.commit()

    return CheckoutResponse(
        billing_id=billing["id"],
        checkout_url=billing["url"],
        plan=plan.name,
        price_brl=plan.price_brl,
    )


@router.get("/me", response_model=SubscriptionOut, summary="Retorna assinatura ativa do usuário")
async def my_subscription(
    user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Retorna o status e validade da assinatura ativa do usuário autenticado."""
    user_id: str = user.get("sub", "")
    sub = await _get_active_subscription(user_id, session)
    if not sub:
        return SubscriptionOut(status="inactive")

    # Carregar plano relacionado
    await session.refresh(sub, ["plan"])
    return SubscriptionOut(
        status="active",
        plan=sub.plan.name if sub.plan else None,
        expires_at=sub.expires_at,
    )


@router.get("/support", summary="Retorna histórico de chat de suporte do usuário")
async def get_support_history(
    user: dict = Depends(get_current_user),
    repo: AdminRepository = Depends(get_admin_repository),
):
    """Retorna todas as mensagens de suporte do usuário autenticado."""
    user_id: str = user.get("sub", "")
    return await repo.get_chat_history(user_id)


@router.post("/support", summary="Envia mensagem para o suporte")
async def send_support_message(
    content: str,
    user: dict = Depends(get_current_user),
    repo: AdminRepository = Depends(get_admin_repository),
):
    """Envia uma nova mensagem para a equipe de suporte."""
    user_id: str = user.get("sub", "")
    if not content:
        raise HTTPException(status_code=400, detail="Conteúdo não pode ser vazio")

    msg = await repo.add_message(user_id=user_id, content=content, is_from_admin=False)
    return msg


def _verify_signature(body: bytes, signature: str) -> bool:
    """
    Valida o X-Webhook-Signature da AbacatePay v2: HMAC-SHA256 do corpo bruto, em base64,
    com a chave pública publicada na documentação (ABACATEPAY_PUBLIC_KEY).
    Sem chave configurada ou sem header, a verificação é pulada (o webhookSecret continua valendo).
    """
    key = settings.abacatepay_public_key
    if not key or not signature:
        return True
    expected = base64.b64encode(hmac.new(key.encode(), body, hashlib.sha256).digest()).decode()
    return hmac.compare_digest(expected, signature)


def _checkout_id_candidates(payload: dict) -> list[str]:
    """
    Coleta possíveis ids de checkout no payload do evento (o formato exato de `data` varia por
    evento): valores de chaves `id`/`checkoutId`/`billingId` e qualquer texto com prefixo `bill_`.
    """
    found: list[str] = []

    def walk(node, key=""):
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, k)
        elif isinstance(node, list):
            for item in node:
                walk(item, key)
        elif isinstance(node, str) and node:
            if node.startswith("bill_") or key in ("checkoutId", "billingId"):
                found.append(node)

    walk(payload.get("data", {}))
    data = payload.get("data") or {}
    for holder in (data, data.get("checkout") or {}, data.get("billing") or {}):
        if isinstance(holder, dict) and isinstance(holder.get("id"), str):
            found.append(holder["id"])
    return list(dict.fromkeys(found))  # sem duplicatas, preservando a ordem


async def _find_subscription(session: AsyncSession, candidates: list[str]):
    for candidate in candidates:
        stmt = select(UserSubscription).where(UserSubscription.abacatepay_billing_id == candidate)
        sub = (await session.execute(stmt)).scalar_one_or_none()
        if sub is not None:
            return sub
    return None


@router.post("/webhook", summary="Receptor de webhooks AbacatePay (API v2)")
async def webhook(
    request: Request,
    webhook_secret: str = Query(..., alias="webhookSecret"),
    session: AsyncSession = Depends(get_session),
):
    """
    Recebe notificações da AbacatePay v2 (`checkout.completed`, `checkout.refunded`).
    Segurança:
      1. webhookSecret na query string (obrigatório)
      2. X-Webhook-Signature (HMAC-SHA256 base64) quando a chave pública estiver configurada
      3. O pagamento é CONFIRMADO consultando o checkout na API (status PAID); o corpo do
         webhook sozinho nunca ativa uma assinatura.
    """
    if not _verify_webhook_secret(webhook_secret):
        raise HTTPException(status_code=401, detail="Webhook secret inválido.")

    body_bytes = await request.body()
    signature = request.headers.get("X-Webhook-Signature", "")
    if signature and not _verify_signature(body_bytes, signature):
        raise HTTPException(status_code=401, detail="Assinatura inválida.")

    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Payload inválido.")

    event: str = payload.get("event", "")
    dev_mode: bool = payload.get("devMode", False)
    logger.info(
        f"Webhook AbacatePay recebido: event={event} devMode={dev_mode} id={payload.get('id')}"
    )

    candidates = _checkout_id_candidates(payload)

    if event in ("checkout.completed", "billing.paid"):
        if not candidates:
            return {"ok": True, "msg": "id do checkout ausente, ignorado"}

        sub = await _find_subscription(session, candidates)
        if sub is None:
            logger.warning(f"Webhook {event} sem assinatura pendente (ids={candidates}).")
            return {"ok": True, "msg": "assinatura não encontrada"}

        if sub.status == "active":
            return {"ok": True, "msg": "já processado"}  # idempotência (retentativas)

        # Confirma o pagamento direto na AbacatePay antes de liberar acesso
        try:
            checkout = await abacatepay_client.get_checkout(sub.abacatepay_billing_id)
        except Exception as e:
            logger.error(f"Falha ao confirmar checkout {sub.abacatepay_billing_id}: {e}")
            # 5xx faz a AbacatePay tentar de novo (até 7 vezes em ~18 h)
            raise HTTPException(status_code=502, detail="Não foi possível confirmar o pagamento.")

        if str(checkout.get("status", "")).upper() != "PAID":
            logger.warning(
                f"Checkout {sub.abacatepay_billing_id} com status {checkout.get('status')}: "
                "não ativado."
            )
            return {"ok": True, "msg": "pagamento não confirmado"}

        plan_result = await session.get(SubscriptionPlan, sub.plan_id)
        now = datetime.now(timezone.utc)
        sub.status = "active"
        sub.starts_at = now
        sub.expires_at = now + timedelta(days=plan_result.duration_days if plan_result else 30)
        sub.updated_at = now

        await session.commit()
        logger.info(f"Assinatura {sub.id} ativada para user {sub.user_id} até {sub.expires_at}")

    elif event in ("checkout.refunded", "checkout.disputed"):
        # v2: estorno ou disputa REVOGA o acesso, inclusive de assinatura já ativa
        sub = await _find_subscription(session, candidates)
        if sub and sub.status != "canceled":
            now = datetime.now(timezone.utc)
            was_active = sub.status == "active"
            sub.status = "canceled"
            sub.updated_at = now
            if was_active:
                sub.expires_at = now
            await session.commit()
            logger.info(f"Assinatura {sub.id} cancelada por evento {event} (ativa={was_active})")

    elif event in ("billing.refunded", "billing.failed"):
        # eventos legados da v1: mantém o comportamento anterior (não revoga assinatura ativa)
        sub = await _find_subscription(session, candidates)
        if sub and sub.status != "active":
            sub.status = "canceled"
            sub.updated_at = datetime.now(timezone.utc)
            await session.commit()
            logger.info(f"Assinatura {sub.id} cancelada por evento {event}")

    return {"ok": True}

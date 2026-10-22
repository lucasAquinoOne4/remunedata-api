"""
app/api/endpoints/admin.py
Rotas administrativas para gestão de usuários, faturamento e suporte.
"""

import asyncio
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_admin_repository, get_admin_user, get_session
from app.core import locks
from app.core.cache import RedisCache, get_cache
from app.core.config import settings
from app.persistence.admin_repository import AdminRepository
from app.persistence.models import ExecutionAnnual
from app.services import people
from app.workers.tasks import (
    collect_annual_task,
    collect_estadual_month_task,
    collect_federal_month_task,
    collect_senado_month_task,
)

router = APIRouter()


@router.get("/stats")
async def get_admin_stats(
    admin: dict = Depends(get_admin_user), repo: AdminRepository = Depends(get_admin_repository)
):
    """Retorna métricas globais de faturamento e usuários."""
    return await repo.get_overall_stats()


@router.get("/users")
async def list_users(
    admin: dict = Depends(get_admin_user),
    repo: AdminRepository = Depends(get_admin_repository),
    limit: int = 50,
    offset: int = 0,
):
    """Lista usuários e seus status de assinatura."""
    return await repo.list_users_admin(limit=limit, offset=offset)


@router.post("/users/{user_id}/reset-password")
async def reset_user_password(
    user_id: str,
    email: str,  # Precisamos do e-mail para o Supabase
    admin: dict = Depends(get_admin_user),
):
    """Dispara e-mail de recuperação de senha via Admin API do Supabase."""
    async with httpx.AsyncClient() as client:
        # Nota: O Supabase exige a Service Role Key para operações administrativas
        response = await client.post(
            f"{settings.supabase_url}/auth/v1/recover",
            json={"email": email},
            headers={
                "apikey": settings.supabase_service_role_key,
                "Content-Type": "application/json",
            },
        )

        if response.status_code != 200:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Erro ao disparar recuperação no Supabase: {response.text}",
            )

    return {"ok": True, "message": f"E-mail de recuperação enviado para {email}"}


@router.get("/support")
async def list_support_chats(
    admin: dict = Depends(get_admin_user), repo: AdminRepository = Depends(get_admin_repository)
):
    """Lista conversas de suporte agrupadas por usuário."""
    return await repo.get_support_chats()


@router.get("/support/{user_id}")
async def get_chat_history(
    user_id: str,
    admin: dict = Depends(get_admin_user),
    repo: AdminRepository = Depends(get_admin_repository),
):
    """Retorna o histórico completo de chat de um usuário."""
    return await repo.get_chat_history(user_id)


@router.post("/support/{user_id}/reply")
async def reply_to_user(
    user_id: str,
    content: str,  # Pode ser trocado por um schema Pydantic se crescer
    admin: dict = Depends(get_admin_user),
    repo: AdminRepository = Depends(get_admin_repository),
):
    """Envia uma resposta administrativa para o usuário."""
    if not content:
        raise HTTPException(status_code=400, detail="Conteúdo não pode ser vazio")

    msg = await repo.add_message(user_id=user_id, content=content, is_from_admin=True)
    return msg


# ─── Carga de dados (somente admin) ──────────────────────────────────────────


@router.get("/loads", summary="Status das cargas de dados por fonte")
async def loads_status(
    admin: dict = Depends(get_admin_user),
    session: AsyncSession = Depends(get_session),
    cache: RedisCache = Depends(get_cache),
):
    """Meses carregados, total de registros, último mês e indicador 'em execução' por fonte."""
    cached = await cache.get("remuneration:admin_loads")
    if cached is None:
        fontes = []
        for fonte in people.SOURCES:
            s_ = people.SPECS[fonte]
            rows = (
                await session.execute(
                    select(s_.ano, s_.mes, func.count())
                    .where(*s_.extra)
                    .group_by(s_.ano, s_.mes)
                    .order_by(s_.ano.desc(), s_.mes.desc())
                    .limit(36)
                )
            ).all()
            meses = [
                {"ano": int(a), "mes": f"{int(m):02d}", "registros": int(n)} for a, m, n in rows
            ]
            fontes.append(
                {
                    "fonte": fonte,
                    "total_registros": sum(m["registros"] for m in meses),
                    "meses": meses,
                    "ultimo": {"ano": meses[0]["ano"], "mes": meses[0]["mes"]} if meses else None,
                }
            )
        recent = (
            (
                await session.execute(
                    select(ExecutionAnnual).order_by(ExecutionAnnual.id.desc()).limit(10)
                )
            )
            .scalars()
            .all()
        )
        cached = {
            "fontes": fontes,
            "coleta_df": [
                {
                    "id": e.id,
                    "ano": e.ano_exercicio,
                    "status": e.status,
                    "meses_processados": e.total_meses_processados,
                    "registros": e.total_registros_coletados,
                }
                for e in recent
            ],
        }
        await cache.set("remuneration:admin_loads", cached, ttl=120)
    # "em execução" não é cacheado: vem direto do Redis
    for item in cached["fontes"]:
        item["em_execucao"] = await asyncio.to_thread(locks.is_running, item["fonte"])
    return cached


@router.post("/loads/{fonte}", summary="Dispara uma carga de dados")
async def trigger_load(
    fonte: str,
    ano: int = Query(..., ge=2000, le=2100),
    mes: Optional[int] = Query(None, ge=1, le=12),
    force: bool = Query(False, description="Recarrega mesmo que o mês já esteja no banco"),
    categoria: str = Query("CIVIL", pattern="^(CIVIL|MILITAR|BACEN)$", description="Só FEDERAL"),
    admin: dict = Depends(get_admin_user),
):
    """DF: coleta o ano inteiro | SENADO e FEDERAL: um mês (ano + mes)."""
    fonte = fonte.upper()
    if fonte not in people.SOURCES:
        raise HTTPException(status_code=404, detail="Fonte desconhecida.")
    if await asyncio.to_thread(locks.is_running, fonte):
        return {"task_id": None, "status": "skipped", "detalhe": "Já existe uma carga em execução."}

    if fonte == "DF":
        result = collect_annual_task.delay(ano)
    else:
        if mes is None:
            raise HTTPException(status_code=422, detail="Informe o mês (parâmetro 'mes').")
        if fonte == "SENADO":
            result = collect_senado_month_task.delay(ano, mes)
        elif fonte in people.STATE_SOURCES:
            result = collect_estadual_month_task.delay(fonte, ano, mes, force)
        else:
            result = collect_federal_month_task.delay(ano, mes, force, categoria)
    return {"task_id": result.id, "status": "queued"}

"""
app/api/deps.py
Dependências do FastAPI para injeção de repositórios e sessão de banco.
"""

from datetime import datetime, timezone

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import verify_token
from app.core.config import settings
from app.core.logging import get_logger
from app.persistence.admin_repository import AdminRepository
from app.persistence.models import UserSubscription
from app.persistence.repositories import (
    EstadualRepository,
    ExecutionRepository,
    FederalRepository,
    RemunerationRepository,
    SenadoRepository,
)
from app.persistence.session import get_session

logger = get_logger(__name__)

security = HTTPBearer()


def get_execution_repository(session: AsyncSession = Depends(get_session)) -> ExecutionRepository:
    """Dependency for ExecutionRepository."""
    return ExecutionRepository(session)


def get_federal_repository(
    session: AsyncSession = Depends(get_session),
) -> FederalRepository:
    """Dependency for FederalRepository."""
    return FederalRepository(session)


def get_senado_repository(
    session: AsyncSession = Depends(get_session),
) -> SenadoRepository:
    """Dependency for SenadoRepository."""
    return SenadoRepository(session)


def get_remuneration_repository(
    session: AsyncSession = Depends(get_session),
) -> RemunerationRepository:
    """Dependency for RemunerationRepository."""
    return RemunerationRepository(session)


def get_admin_repository(session: AsyncSession = Depends(get_session)) -> AdminRepository:
    """Dependency for AdminRepository."""
    return AdminRepository(session)


async def get_current_user(token: HTTPAuthorizationCredentials = Depends(security)) -> dict:
    """
    Dependência que valida o token JWT do Supabase e retorna o payload do usuário.
    """
    return verify_token(token.credentials)


async def get_admin_user(user: dict = Depends(get_current_user)) -> dict:
    """
    Dependência que valida se o usuário atual é um administrador.
    Valida contra o e-mail definido na lista de ADMIN_EMAILS.
    """
    email = user.get("email")
    if not email or email not in settings.admin_emails:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Acesso restrito a administradores.",
        )
    return user


async def require_active_subscription(
    user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """
    DependÃªncia que valida se o usuÃ¡rio possui assinatura ativa.
    ADMINISTRADORES pulam esta verificaÃ§Ã£o e tÃªm acesso total.
    """
    email = user.get("email")
    user_id: str = user.get("sub", "")

    # 1. Se for administrador, libera direto
    if email and email in settings.admin_emails:
        logger.info(f"Acesso ADMINISTRADOR liberado para {email} (Sem checagem de assinatura)")
        return user

    # 2. UsuÃ¡rio comum: verifica assinatura ativa no banco
    now = datetime.now(timezone.utc)
    stmt = select(UserSubscription).where(
        UserSubscription.user_id == user_id,
        UserSubscription.status == "active",
        UserSubscription.expires_at > now,
    )
    result = await session.execute(stmt)
    sub = result.scalar_one_or_none()

    if sub is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Assinatura ativa necessÃ¡ria para acessar este recurso. Assine em /planos.",
        )
    return user


def get_estadual_repository_factory(session: AsyncSession = Depends(get_session)):
    """Fábrica de EstadualRepository por UF (a UF vem do parâmetro ?fonte=)."""
    return lambda uf: EstadualRepository(session, uf)

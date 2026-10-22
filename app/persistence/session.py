"""
app/persistence/session.py
Configuração da engine e do factory de sessões assíncronas do SQLAlchemy 2.0.
Usa as configurações definidas em app/core/config.py.
"""

from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings

settings = get_settings()

# Engine assíncrona usando asyncpg
async_engine = create_async_engine(
    settings.database_url,
    echo=settings.is_development,
    future=True,
    hide_parameters=True,  # erros não vazam valores (CPF/nomes) nos logs
    pool_pre_ping=True,
    # Aumentar pool para suportar cargas do coletor
    pool_size=10,
    max_overflow=20,
)

# Factory para gerar AsyncSession
async_session_maker = async_sessionmaker(
    bind=async_engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """
    Dependency para obter uma sessão assíncrona do banco de dados.
    Pode ser usada no FastAPI Depends(get_session).
    """
    async with async_session_maker() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()

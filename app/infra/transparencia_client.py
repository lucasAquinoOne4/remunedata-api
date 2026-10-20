"""
app/infra/transparencia_client.py
Cliente para consumo da API do Portal da Transparência do DF.
Usa httpx para requisições assíncronas.
"""

import asyncio
from typing import Any, Dict, Optional

import httpx
from circuitbreaker import circuit

from app.core import locks
from app.core.config import get_settings
from app.core.logging import get_logger, log_extra

settings = get_settings()
logger = get_logger(__name__)

_MAX_429_RETRIES = 3
_MAX_BAN_WAIT_SECONDS = 1800.0


class TransparenciaClient:
    """
    Cliente de integração com a API de remuneração do DF.

    A API base é: https://www.transparencia.df.gov.br/api/remuneracao
    """

    def __init__(self, timeout: Optional[int] = None):
        self.base_url = f"{settings.transparencia_api_base_url}/remuneracao"
        self.timeout = timeout or settings.transparencia_timeout_seconds
        self.headers = {
            "Accept": "application/json, text/plain, */*",
            "x-client-id": settings.transparencia_client_id,
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        }

    @circuit(failure_threshold=5, recovery_timeout=60, expected_exception=httpx.HTTPStatusError)
    async def get_remuneracao(
        self,
        ano: int,
        mes: str,
        page: int = 0,
        size: Optional[int] = None,
        nome: str = "",
    ) -> Dict[str, Any]:
        """
        Consulta a API de remuneração para uma competência e página específica.

        Args:
            ano: Ano de exercício (ex: 2025)
            mes: Mês de referência (ex: "06")
            page: Índice da página (0-indexed)
            size: Quantidade de registros por página (máx validado: 150)
            nome: Filtro opcional por nome do servidor

        Returns:
            Dicionário com a resposta JSON da API.

        Raises:
            httpx.HTTPStatusError: Se a API retornar erro de status (ex: 4xx, 5xx)
            httpx.RequestError: Se houver erro de rede ou timeout
        """
        if size is None:
            size = settings.transparencia_page_size

        params = {
            "anoExercicio": str(ano),
            "mesReferencia": str(mes),
            "page": str(page),
            "size": str(size),
            "nomeServidor": nome,
            # Parâmetros observados na chamada real que podem ser necessários
            "busy": "true",
            "editing": "true",
        }

        logger.debug(
            "Consultando API Transparência", extra=log_extra(ano=ano, mes=mes, page=page, size=size)
        )

        async with httpx.AsyncClient(timeout=self.timeout, headers=self.headers) as client:
            # 429 (rate limit): o portal manda um Retry-After REAL (contagem regressiva de bloqueio).
            # Respeita o bloqueio globalmente; insistir durante ele só o prolonga.
            for attempt in range(_MAX_429_RETRIES + 1):
                remaining = await asyncio.to_thread(locks.ban_remaining)
                if remaining > 0:
                    logger.info(
                        "Portal em bloqueio de taxa, aguardando",
                        extra=log_extra(mes=mes, page=page, wait=round(remaining)),
                    )
                    await asyncio.sleep(min(remaining, _MAX_BAN_WAIT_SECONDS) + 2)

                response = await client.get(self.base_url, params=params)
                if response.status_code != 429 or attempt == _MAX_429_RETRIES:
                    break
                try:
                    wait = float(response.headers.get("Retry-After", ""))
                except ValueError:
                    wait = 30.0 * (attempt + 1)
                wait = min(wait, _MAX_BAN_WAIT_SECONDS)
                await asyncio.to_thread(locks.set_ban, wait)
                logger.warning(
                    "429 da API Transparência, bloqueio registrado",
                    extra=log_extra(mes=mes, page=page, wait=wait),
                )

            # Lança exceção para status 4xx/5xx
            response.raise_for_status()

            return response.json()

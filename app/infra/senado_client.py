"""
app/infra/senado_client.py
Cliente da API de dados abertos administrativos do Senado Federal.

Cada mês de remuneração vem numa única resposta JSON (~11 mil registros);
não há paginação nem chave de API.
"""

from typing import Any, Dict, List

import httpx
from circuitbreaker import circuit

from app.core.config import get_settings
from app.core.logging import get_logger

settings = get_settings()
logger = get_logger(__name__)


class SenadoClient:
    """Cliente de integração com adm.senado.gov.br/adm-dadosabertos."""

    def __init__(self, timeout: int | None = None):
        self.base_url = settings.senado_api_base_url.rstrip("/")
        self.timeout = timeout or settings.senado_timeout_seconds
        self.headers = {"Accept": "application/json"}

    async def _get_list(self, path: str) -> List[Dict[str, Any]]:
        async with httpx.AsyncClient(timeout=self.timeout, headers=self.headers) as client:
            response = await client.get(f"{self.base_url}{path}")
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, list):
                raise ValueError(
                    f"Resposta inesperada do Senado em {path}: esperado lista, veio {type(data).__name__}"
                )
            return data

    @circuit(failure_threshold=5, recovery_timeout=60, expected_exception=httpx.HTTPError)
    async def get_remuneracoes(self, ano: int, mes: int) -> List[Dict[str, Any]]:
        """Folha de pagamento de um mês (lista vazia se o mês ainda não foi publicado)."""
        return await self._get_list(f"/servidores/remuneracoes/{ano}/{mes}")

    @circuit(failure_threshold=5, recovery_timeout=60, expected_exception=httpx.HTTPError)
    async def get_servidores(self) -> List[Dict[str, Any]]:
        """Cadastro completo (ativos, aposentados e desligados)."""
        return await self._get_list("/servidores/servidores")

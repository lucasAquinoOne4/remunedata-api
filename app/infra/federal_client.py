"""
app/infra/federal_client.py
Download mensal (ZIP) dos servidores civis do Executivo federal (SIAPE), Portal da Transparência.

Uma carga por mês, sem chave de API e sem paginação: o ZIP traz Cadastro e Remuneracao do mês
inteiro (~83 MB). Muito mais leve para o portal do que a API por servidor (uma chamada por pessoa
por mês, centenas de milhares por mês).
"""

from pathlib import Path

import httpx

from app.core.config import get_settings
from app.core.logging import get_logger

settings = get_settings()
logger = get_logger(__name__)

_MIN_ZIP_BYTES = 1_000_000  # um ZIP real tem dezenas de MB; abaixo disso é página de erro


class FederalDownloadClient:
    """Cliente do download em lote do Portal da Transparência federal."""

    def __init__(self, timeout: int = 900):
        self.base_url = settings.portal_federal_download_base_url.rstrip("/")
        self.timeout = timeout
        self.headers = {"User-Agent": "Mozilla/5.0 (RemuneData)", "Accept": "*/*"}

    def url(self, ano: int, mes: int) -> str:
        return f"{self.base_url}/{ano}{mes:02d}_Servidores_SIAPE"

    @staticmethod
    def _is_zip(response: httpx.Response) -> bool:
        ctype = response.headers.get("content-type", "").lower()
        size = int(response.headers.get("content-length", "0") or 0)
        return response.status_code == 200 and "zip" in ctype and size >= _MIN_ZIP_BYTES

    async def is_published(self, ano: int, mes: int) -> bool:
        """
        O mês publicado devolve 200 com o ZIP; o não publicado devolve 302 e depois 403 (XML).
        Uma requisição HEAD, sem baixar nada.
        """
        async with httpx.AsyncClient(
            timeout=60, headers=self.headers, follow_redirects=True
        ) as client:
            response = await client.head(self.url(ano, mes))
        return self._is_zip(response)

    async def download(self, ano: int, mes: int, destination: Path) -> int:
        """Baixa o ZIP do mês em streaming para `destination`. Devolve os bytes gravados."""
        written = 0
        async with httpx.AsyncClient(
            timeout=self.timeout, headers=self.headers, follow_redirects=True
        ) as client:
            async with client.stream("GET", self.url(ano, mes)) as response:
                if (
                    response.status_code != 200
                    or "zip" not in response.headers.get("content-type", "").lower()
                ):
                    raise RuntimeError(
                        f"Download federal {mes:02d}/{ano} indisponível "
                        f"(HTTP {response.status_code}, {response.headers.get('content-type')})"
                    )
                with destination.open("wb") as fh:
                    async for chunk in response.aiter_bytes(1024 * 1024):
                        fh.write(chunk)
                        written += len(chunk)
        logger.info(f"Federal {mes:02d}/{ano}: ZIP baixado ({written / 1e6:.1f} MB).")
        return written

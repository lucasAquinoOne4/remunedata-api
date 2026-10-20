"""
app/infra/abacatepay_client.py
Cliente HTTP assíncrono para a API AbacatePay v2.
Documentação: https://docs.abacatepay.com

Na v2 um checkout referencia PRODUTOS já cadastrados (`items: [{id, quantity}]`).
Por isso cada plano vira um produto (criado sob demanda e reaproveitado).
"""

import logging
from typing import Any

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)


class AbacatePayClient:
    """
    Encapsula as chamadas à API AbacatePay v2.
    Autenticação: Bearer Token no header Authorization (chave v2).
    """

    def __init__(self):
        self._base_url = settings.abacatepay_base_url
        self._headers = {
            "Authorization": f"Bearer {settings.abacatepay_api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        # externalId do produto -> id do produto na AbacatePay (cache em memória)
        self._product_ids: dict[str, str] = {}

    async def _request(
        self, method: str, path: str, *, json: dict | None = None, params: dict | None = None
    ) -> dict[str, Any]:
        """Executa uma chamada autenticada e retorna o corpo completo da resposta."""
        url = f"{self._base_url}{path}"
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.request(
                method, url, json=json, params=params, headers=self._headers
            )
            if response.status_code >= 400:
                try:
                    logger.error(f"ERRO ABACATEPAY ({response.status_code}): {response.json()}")
                except Exception:
                    logger.error(f"ERRO ABACATEPAY ({response.status_code}): {response.text}")
            response.raise_for_status()
            body = response.json()
            if body.get("error"):
                raise ValueError(f"AbacatePay error: {body['error']}")
            return body

    async def _post(self, path: str, payload: dict) -> dict[str, Any]:
        """POST autenticado; retorna o campo 'data'."""
        body = await self._request("POST", path, json=payload)
        return body.get("data", body)

    async def _get(self, path: str, params: dict | None = None) -> dict[str, Any]:
        """GET autenticado; retorna o campo 'data'."""
        body = await self._request("GET", path, params=params)
        return body.get("data", body)

    async def create_customer(
        self,
        name: str,
        email: str,
        cellphone: str,
        tax_id: str,
    ) -> dict[str, Any]:
        """
        Cria (ou recupera, pois o cliente é único por CPF/CNPJ) um cliente.
        POST /v2/customers/create
        """
        payload = {
            "name": name,
            "email": email,
            "cellphone": cellphone,
            "taxId": tax_id,
        }
        return await self._post("/customers/create", payload)

    @staticmethod
    def product_external_id(plan_slug: str, price_cents: int) -> str:
        """externalId estável do produto de um plano (inclui o preço: mudou o preço, novo produto)."""
        return f"remunedata-{plan_slug}-{price_cents}"

    async def _find_product_id(self, external_id: str) -> str | None:
        """Procura um produto pelo externalId na listagem."""
        cursor: str | None = None
        for _ in range(20):  # teto de segurança de páginas
            params = {"next": cursor} if cursor else None
            body = await self._request("GET", "/products/list", params=params)
            for product in body.get("data") or []:
                if product.get("externalId") == external_id:
                    return product["id"]
            pagination = body.get("pagination") or {}
            cursor = pagination.get("next")
            if not pagination.get("hasMore") or not cursor:
                break
        return None

    async def ensure_product(
        self, plan_slug: str, plan_name: str, plan_description: str, price_cents: int
    ) -> str:
        """
        Garante que o plano exista como produto na AbacatePay e devolve o id dele.
        POST /v2/products/create (400 "already exists" -> procura na listagem).
        """
        external_id = self.product_external_id(plan_slug, price_cents)
        if external_id in self._product_ids:
            return self._product_ids[external_id]

        product_id = await self._find_product_id(external_id)
        if product_id is None:
            try:
                data = await self._post(
                    "/products/create",
                    {
                        "externalId": external_id,
                        "name": plan_name,
                        "description": plan_description,
                        "price": price_cents,
                        "currency": "BRL",
                    },
                )
                product_id = data["id"]
            except (httpx.HTTPStatusError, ValueError):
                # Corrida: outro processo criou entre a busca e a criação
                product_id = await self._find_product_id(external_id)
                if product_id is None:
                    raise

        self._product_ids[external_id] = product_id
        return product_id

    async def create_checkout(
        self,
        customer_id: str,
        product_id: str,
        external_id: str,
        plan_slug: str,
    ) -> dict[str, Any]:
        """
        Cria um checkout de pagamento único via PIX + Cartão.
        POST /v2/checkouts/create
        Retorna: { id, url, status, ... }
        """
        methods = [m.strip().upper() for m in settings.abacatepay_methods.split(",") if m.strip()]
        payload = {
            "items": [{"id": product_id, "quantity": 1}],
            "methods": methods or ["PIX"],
            "returnUrl": f"{settings.front_url}/planos",
            "completionUrl": f"{settings.front_url}/assinatura/sucesso?billing={external_id}",
            "customerId": customer_id,
            "externalId": external_id,
            "metadata": {"plan": plan_slug},
        }
        try:
            return await self._post("/checkouts/create", payload)
        except httpx.HTTPStatusError as e:
            # Loja sem um dos meios habilitado (ex.: cartão ainda em análise): tenta sem ele.
            unavailable = self._unavailable_method(e)
            if unavailable and unavailable in payload["methods"] and len(payload["methods"]) > 1:
                logger.warning(f"Meio {unavailable} indisponível na loja; seguindo sem ele.")
                payload["methods"] = [m for m in payload["methods"] if m != unavailable]
                return await self._post("/checkouts/create", payload)
            raise

    @staticmethod
    def _unavailable_method(error: httpx.HTTPStatusError) -> str | None:
        """Extrai o meio de pagamento de erros do tipo 'CARD is not available for this store'."""
        try:
            message = str(error.response.json().get("error", ""))
        except Exception:
            return None
        if "is not available for this store" in message:
            return message.split(" ", 1)[0].upper()
        return None

    async def get_checkout(self, checkout_id: str) -> dict[str, Any]:
        """
        Busca um checkout pelo ID (usado para confirmar o pagamento direto na fonte).
        GET /v2/checkouts/get?id=...
        """
        return await self._get("/checkouts/get", params={"id": checkout_id})


# Instância global compartilhada
abacatepay_client = AbacatePayClient()

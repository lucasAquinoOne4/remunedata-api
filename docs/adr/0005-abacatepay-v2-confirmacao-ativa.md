# ADR 0005 — AbacatePay v2 com confirmação ativa do pagamento

- **Status:** Aceito (2026-10)
- **Implementação:** `app/infra/abacatepay_client.py`, `app/api/endpoints/subscriptions.py`

## Contexto
As chaves novas da AbacatePay são da API v2 e a v1 respondeu `401 API key version mismatch` (o webhook v1 aparece como "indisponível em breve"). A v2 exige produtos pré-cadastrados no checkout e muda eventos e assinatura.

## Decisão
- Cada plano vira um produto (`externalId = remunedata-<slug>-<centavos>`), criado sob demanda e reaproveitado (busca na listagem; 400 "already exists" refaz a busca). O preço faz parte do id: mudou o preço, novo produto.
- Checkout v2 por PIX e cartão (`ABACATEPAY_METHODS`); se a loja não tiver um meio habilitado ("CARD is not available for this store"), segue sem ele.
- Webhook: `webhookSecret` na query é obrigatório e fail-closed em produção; `X-Webhook-Signature` (HMAC-SHA256, base64) só é validado se `ABACATEPAY_PUBLIC_KEY` estiver configurada.
- **O corpo do webhook nunca ativa acesso.** `checkout.completed` consulta `GET /v2/checkouts/get` e só ativa com status `PAID`; falha na consulta devolve 502 para a AbacatePay tentar de novo (até 7 vezes em ~18 h). O formato de `data` varia por evento, então os ids de checkout são extraídos do payload de forma tolerante e confrontados com o banco.
- `checkout.refunded` e `checkout.disputed` revogam a assinatura, inclusive ativa. Os eventos legados `billing.*` continuam aceitos com o comportamento antigo.

## Alternativas descartadas
Confiar no payload do evento; manter a v1.

## Consequências
Dependência da API da AbacatePay no momento do webhook. A "chave pública" de assinatura não é autenticação (é pública); a segurança vem do segredo na query e da confirmação ativa. Pendente validar um pagamento de teste de ponta a ponta.

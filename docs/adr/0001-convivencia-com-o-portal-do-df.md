# ADR 0001 — Convivência com o Portal da Transparência do DF

- **Status:** Aceito (2026-10)
- **Implementação:** `app/infra/transparencia_client.py`, `app/core/locks.py`, `app/core/config.py`

## Contexto
Os docs 02, 03 e 05 validaram a API do portal com `X-BROWSER-VERSION` e trataram `size=150` como "provável teto". Em produção o portal passou a exigir o header `x-client-id` (sem ele responde 400) e a limitar a taxa por IP. O 429 vem com um `Retry-After` **real**: uma contagem regressiva de bloqueio (medida: 713 s decrescendo até liberar). Insistir durante o bloqueio o prolonga.

## Decisão
- Enviar `x-client-id` (configurável em `TRANSPARENCIA_CLIENT_ID`, valor não secreto) e um User-Agent de navegador.
- `size=150` é fixo (o servidor devolve 150 mesmo se pedirem 500); a coleta lê `TRANSPARENCIA_PAGE_SIZE`.
- Em 429: respeitar o `Retry-After` (teto de 1800 s), no máximo 3 retentativas, e registrar um **bloqueio global no Redis** (`ratelimit:df:until`) que todo worker consulta antes de cada requisição.
- Esgotadas as retentativas, levantar `PortalRateLimited`, que **não** é `httpx.HTTPStatusError`: o circuit breaker não conta bloqueio de taxa como falha (senão abriria e furaria a espera).
- Cadência medida e sustentável: 1 página por vez com pausa de 2 s (`TRANSPARENCIA_BATCH_SIZE=1`, `TRANSPARENCIA_REQUEST_DELAY_SECONDS=2`), cerca de 12 páginas/min e 1.800 registros/min.

## Alternativas descartadas
- Backoff exponencial curto: mantém a janela de limite cheia e prolonga o bloqueio.
- Páginas maiores que 150: ignorado pelo servidor.
- Mais paralelismo: 2 páginas em paralelo já provocam 429; cada bloqueio custa mais que o ganho.

## Consequências
Coleta lenta e previsível (o ano completo leva dezenas de horas). Dependência do Redis para a cortesia entre workers. O limite é por IP: outro IP tem cota própria.

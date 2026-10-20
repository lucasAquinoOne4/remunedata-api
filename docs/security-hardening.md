# Segurança e Hardening

> **Atualização (2026-10-20):** a API já tem rate limit por código (`RateLimiter` em `/remuneration`) e circuit breaker nos clientes; em produção `/docs`, `/openapi.json` e `/debug-routes` ficam desligados. Ver [ADR 0006](adr/0006-deploy-ec2-free-tier-agendado.md).

Este documento detalha as medidas de segurança implementadas para a operação em produção da API `api-remunedata.com.br`.

## 1. CORS (Cross-Origin Resource Sharing)
A API está configurada para aceitar apenas origens autorizadas.
- **Configuração:** `CORS_ORIGINS` (váriável de ambiente).
- **Padrão local:** `["*"]` (aberto para desenvolvimento).
- **Produção (Sugestão):** `["https://remunedata.com.br", "https://www.remunedata.com.br"]`.

## 2. Proxy Reverso e Cloudflare
Como a API opera atrás do proxy laranja da Cloudflare:
- **Headers:** A API deve respeitar os headers `X-Forwarded-For` e `X-Forwarded-Proto`.
- **SSL Full (Strict):** Configure a Cloudflare para usar SSL Estrito entre a Cloudflare e o servidor Coolify para garantir encriptação ponta-a-ponta.
- **HSTS:** Recomendado ativar no painel da Cloudflare para forçar HTTPS.

## 3. Isolamento de Processos (Docker)
- **Usuário Não-Root:** O container roda sob o usuário `appuser` (UID 1000), reduzindo o impacto em caso de vulnerabilidade no processo.
- **Multi-stage Build:** A imagem final não contém compiladores ou ferramentas de depuração (`gcc`, `pip-dev`), reduzindo a superfície de ataque.

## 4. Limites de Taxa (Rate Limiting)
Atualmente a API não implementa rate-limiting via código. Recomenda-se configurar no **Painel da Cloudflare** (WAF) limites de requisições por IP para evitar scraping abusivo ou DoS.

## 5. Migrations Seguras
As migrations são executadas via `entrypoint.sh` antes da aplicação subir. 
- Em caso de falha na migration, o container não inicia, evitando que a aplicação rode com schema inconsistente.

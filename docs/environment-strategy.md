# Estratégia de Ambientes e Variáveis

> **Atualização (2026-10-20):** há três bancos (local, hml e test) e `make test` roda isolado no banco `_test`. Ver [ADR 0004](adr/0004-bancos-por-ambiente-e-isolamento-de-testes.md).

O projeto utiliza três níveis de ambiente para garantir isolamento e segurança.

## 1. Ambiente Local (`development`)
- **Arquivo:** `.env` (baseado no `.env.example`).
- **Banco:** Container local (`postgres`).
- **Uso:** Desenvolvimento diário, testes manuais.

## 2. Ambiente de CI (`testing`)
- **Arquivo:** Definido nos workflows do GitHub Actions (`ci-staging.yml`).
- **Banco:** Serviço `postgres` temporário criado pelo GitHub Actions.
- **Uso:** Validação automatizada de código e cobertura.

## 3. Ambiente de Produção (`production`)
- **Gestão:** Painel do **Coolify**.
- **Banco:** PostgreSQL gerenciado ou container persistente no servidor.
- **Segurança:** Nunca use senhas de desenvolvimento em produção.

## Dicionário de Variáveis

| Variável | Local | Produção (Coolify) | Descrição |
|---|---|---|---|
| `DATABASE_URL` | `postgresql+asyncpg://...` | URL Real do DB Prod | Conexão assíncrona da API. |
| `DATABASE_URL_SYNC` | `postgresql+psycopg2://...` | URL Real do DB Prod | Usada pelo Alembic (Migrations). |
| `REDIS_URL` | `redis://localhost:6379/0` | URL do Redis Prod | Broker e Backend do Celery. |
| `APP_ENV` | `development` | `production` | Identifica o ambiente. |
| `LOG_LEVEL` | `DEBUG` | `INFO` | Nível de verbosidade dos logs. |

## Boas Práticas de Segurança
- **Segredos:** Nenhuma variável contendo senhas ou tokens deve ser commitada no repositório.
- **Geração de Senhas:** Para produção, gere senhas fortes de no mínimo 32 caracteres alfanuméricos.
- **Isolamento:** O banco de dados de produção deve ser acessível apenas pela rede interna do servidor ou via VPN/IP restrito.

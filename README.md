# df-remuneration-collector

Ferramenta de RPA para coleta de dados de remuneração dos servidores públicos do Distrito Federal (Portal da Transparência do DF) e do Senado Federal (dados abertos do Senado). A busca aceita `?fonte=DF` ou `?fonte=SENADO`.

---

## Sumario

- [Visao Geral](#visao-geral)
- [Objetivo](#objetivo)
- [Stack Tecnologica](#stack-tecnologica)
- [Metodologia de Desenvolvimento (TDD/DOD)](#metodologia-de-desenvolvimento-tdddod)
- [Instalacao e Configuracao](#instalacao-e-configuracao)
- [Como Rodar os Testes e Cobertura](#como-rodar-os-testes-e-cobertura)
- [Endpoints disponiveis](#endpoints-disponiveis)
- [CI/CD e Deploy](#cicd-e-deploy)

---

## Visao Geral

Este projeto coleta, armazena e expoe dados de remuneração dos servidores públicos do DF a partir da API do Portal da Transparência do DF (https://www.transparencia.df.gov.br/api/remuneracao).

A coleta e feita por competência mensal (anoExercicio + mesReferencia) usando paginação com size=150 (teto validado da API), armazenando os resultados em PostgreSQL com rastreabilidade completa de cada execução.

---

## Objetivo

- Coletar dados de remuneração paginados da API pública do DF.
- Persistir os dados em PostgreSQL com modelo estruturado e rastreável.
- Expor consultas via API REST (FastAPI).
- Permitir exportação em CSV/XLSX com limites de segurança (5k/1k).
- Suportar execução via worker assíncrono (Celery + Redis) e agendamento (Celery Beat).
- Disponibilizar Dashboard Analítico local para visualização de dados.

---

## Stack Tecnologica

- Python 3.12 - Linguagem principal.
- FastAPI + Uvicorn - API REST de alta performance.
- PostgreSQL 16 - Banco de dados relacional.
- SQLAlchemy 2.x (async) - ORM assíncrono.
- Alembic - Gerenciamento de migrations.
- Redis 7 - Broker de mensagens e cache.
- Celery - Processamento distribuído de tarefas.
- httpx - Cliente HTTP assíncrono.
- Pydantic v2 - Validação de dados e configurações.
- Docker + Docker Compose - Containerização e orquestração.
- Supabase Auth - Autenticação baseada em JWT (HS256).
- PyJWT - Validação de tokens no backend.
- pytest + coverage.py - Garantia de qualidade e métricas.

---

## Metodologia de Desenvolvimento (TDD/DOD)

Este projeto foi construído seguindo rigorosos padrões de engenharia de software para garantir estabilidade em ambiente de produção.

### TDD (Test-Driven Development)
O desenvolvimento foi guiado por testes em etapas críticas:
- Regression Testing: Implementação de testes específicos (ex: tests/test_duplication.py) para garantir que correções de bugs (como duplicidade de dados) permaneçam efetivas.
- Mocking: Simulação completa da API externa para testar cenários de timeout, erros HTTP 500 e variações de payload sem depender da rede.
- Batching Logic: Validação da lógica de processamento assíncrono em lotes de 5 páginas para garantir performance sem perda de integridade.

### DOD (Definition of Done)
Uma funcionalidade só é considerada concluída quando atende aos seguintes critérios:
- Cobertura de Testes: Mínimo de 86,0% (Atual: 86,67%).
- Qualidade de Código: Zero erros ou avisos nas ferramentas de linting e formatação (Ruff).
- Idempotência: Verificada a capacidade de re-execução (Clean & Resume) sem gerar dados duplicados ou inconsistentes.
- Documentação: Todas as alterações técnicas e arquiteturais refletidas nos arquivos markdown do repositório.

---

## Instalacao e Configuracao

### 1. Clonar o repositorio
```bash
git clone https://github.com/hericlessssss/df-remuneration-collector.git
cd df-remuneration-collector
```

### 2. Configurar variaveis de ambiente
```bash
cp .env.example .env
```
Edite o arquivo .env com as credenciais do seu banco de dados e Redis.

### 3. Executar com Docker Compose
```bash
docker compose up -d --build
```

---

## Como Rodar os Testes e Cobertura

### Executar todos os testes
```bash
make test
```
`make test` roda sempre no banco isolado `df_remuneration_test` (e no Redis 15), porque a suíte faz `TRUNCATE` nas tabelas. Rodar `pytest` direto contra o banco local é bloqueado de propósito pelo `tests/conftest.py`. Ver [ADR 0004](docs/adr/0004-bancos-por-ambiente-e-isolamento-de-testes.md).

### Verificar Cobertura
```bash
make migrate-test && set -a && . ./.env.test && set +a && \
  docker compose run --rm -e DATABASE_URL -e DATABASE_URL_SYNC -e REDIS_URL -e APP_ENV -e SUPABASE_URL \
  -e SUPABASE_JWT_SECRET -e ADMIN_EMAILS -e PYTHONPATH=. app pytest --cov=app tests/
```
A cobertura atual do projeto e de 86,67%, superando o requisito mínimo de 86%.

---

## Endpoints disponiveis

| Metodo | URL | Descricao |
|---|---|---|
| GET | /api/v1/remuneration/summary | Resumo analítico (Dashboard) |
| GET | /api/v1/remuneration/ | Busca com filtros e paginação |
| POST | /api/v1/executions/ | Inicia nova coleta anual |
| GET | /api/v1/executions/ | Lista histórico de execuções |
| GET | /api/v1/executions/{id}/export | Exportação de dados (CSV/XLSX) |
| GET | /dashboard/ | Interface visual do Dashboard |
| GET | /health | Verificação de integridade do sistema (Público) |

> **Nota:** Todos os endpoints sob `/api/v1/` exigem autenticação via header `Authorization: Bearer <JWT_SUPABASE>`.

---

## CI/CD e Deploy

O projeto utiliza GitHub Actions para integração contínua (CI). O deploy em uso hoje é uma EC2 t3.micro agendada (ver [ADR 0006](docs/adr/0006-deploy-ec2-free-tier-agendado.md) e `deploy/`); o workflow original ainda aponta para o Coolify.
- Build automatizado de imagens Docker.
- Verificação obrigatória de testes e linting antes do deploy.
- Gerenciamento de containers em produção via Coolify.


## Decisões arquiteturais

Ver o índice em [docs/adr/](docs/adr/README.md).

# CI/CD Pipeline — GitHub Actions

> **Atualização (2026-10-20):** o deploy em uso hoje é uma EC2 agendada (ver [ADR 0006](adr/0006-deploy-ec2-free-tier-agendado.md)), não o Coolify descrito abaixo. O job de deploy do `cd-main.yml` ainda aponta para o Coolify.

Este projeto utiliza o GitHub Actions para garantir a qualidade do código e automatizar o deploy.

## Fluxo de Branches

1.  **Feature Branches:** Desenvolvimento local. PRs são abertos para `staging`.
2.  **`staging`:** Branch de integração. Cada push aciona o pipeline de **CI**.
3.  **`main`:** Branch de produção. Cada push aciona o pipeline de **CD** (Build + Deploy).

## Workflows

### 1. CI Staging (`.github/workflows/ci-staging.yml`)
Executado em cada push ou Pull Request para a branch `staging`.
- **Linting:** Verifica estilo e erros estáticos com `ruff`.
- **Testes:** Executa o `pytest` com banco de dados PostgreSQL e Redis em containers auxiliares.
- **Cobertura:** Valida se a cobertura de código é superior a **86%**.

### 2. CD Main (`.github/workflows/cd-main.yml`)
Executado em cada push para a branch `main`.
- **Reuso de CI:** Garante que os testes passam antes de prosseguir.
- **Docker Build:** Constrói a imagem Docker multi-stage (produção).
- **GHCR Push:** Publica a imagem no GitHub Container Registry (`ghcr.io`).
- **Coolify Trigger:** Dispara um Webhook para o Coolify realizar o redeploy automático.

## Segredos Necessários (GitHub Secrets)

Para o funcionamento completo, configure os seguintes segredos no repositório:

| Segredo | Descrição |
|---|---|
| `COOLIFY_WEBHOOK` | URL do Webhook gerado no Coolify para redeploy da aplicação. |
| `GITHUB_TOKEN` | Automático do GitHub, usado para autenticação no GHCR. |

## Como Monitorar
O status de cada execução pode ser acompanhado na aba **Actions** do repositório no GitHub.

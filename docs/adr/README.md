# Registros de Decisão Arquitetural (ADRs)

Decisões que mudaram o sistema depois dos documentos `docs/01`–`15` (que descrevem a descoberta e a validação da fonte do DF). Cada ADR registra contexto, decisão, alternativas descartadas e consequências, com os commits e arquivos que a implementam.

| # | Decisão | Status |
|---|---|---|
| [0001](0001-convivencia-com-o-portal-do-df.md) | Convivência com o Portal da Transparência do DF (identificação, página e rate limit) | Aceito |
| [0002](0002-coleta-serial-idempotente-e-retomavel.md) | Coleta do DF serial, idempotente e retomável | Aceito |
| [0003](0003-senado-fonte-separada-juncao-por-nome.md) | Senado Federal como fonte separada, com junção por nome | Aceito |
| [0004](0004-bancos-por-ambiente-e-isolamento-de-testes.md) | Bancos por ambiente e isolamento da suíte de testes | Aceito |
| [0005](0005-abacatepay-v2-confirmacao-ativa.md) | AbacatePay v2 com confirmação ativa do pagamento | Aceito |
| [0006](0006-deploy-ec2-free-tier-agendado.md) | Deploy em EC2 t3.micro, ligada só no horário de uso | Aceito |
| [0007](0007-busca-por-nome-com-indice-trigram.md) | Busca por nome com índice trigram | Aceito |
| [0008](0008-executivo-federal-por-download-mensal.md) | Executivo federal por download mensal em lote | Aceito |
| [0009](0009-portal-desempenho-cache-e-carga-so-admin.md) | Desempenho do portal, cache em Redis e carga só para admin | Aceito |

O frontend tem o seu próprio ADR em `remunedata-web/docs/adr/`.

Convenção: um arquivo por decisão, numeração sequencial, nunca reescrever uma decisão aceita (criar um ADR novo que a substitua).

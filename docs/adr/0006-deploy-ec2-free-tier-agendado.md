# ADR 0006 — Deploy em EC2 t3.micro, ligada só no horário de uso

- **Status:** Aceito (2026-10). Substitui, na prática, o alvo Coolify descrito em `docs/coolify-setup.md` e `docs/cicd.md`.
- **Implementação:** `deploy/` (user-data, compose, Nginx)

## Contexto
Custo mínimo: uma instância de 1 GB de RAM com Postgres de 8,6 GB, API, worker e beat. Uso entre 05:30 e 01:00 (BRT).

## Decisão
- EC2 t3.micro (`us-east-1c`), disco gp3 de 30 GB, 2 GB de swap, IP elástico (endereço estável sem domínio próprio).
- Docker Compose com `mem_limit` por serviço e rotação de logs (10 MB x 3). Postgres ajustado para 1 GB (`shared_buffers=128MB`, sem paralelismo de manutenção, `shm_size=256m`) e `stop_grace_period` de 90 s.
- Worker com `--concurrency=2`: a coleta do DF (que dura horas, protegida pela trava) não pode bloquear a sincronização do Senado.
- Nginx no host: TLS Let's Encrypt (`remunedata.serveblog.net`), HSTS, cabeçalhos de segurança, `limit_req`, `server_tokens off`, e **sem** expor `/docs` nem `/openapi.json` (a API também os desliga em produção).
- EventBridge Scheduler liga às 05:30 e desliga à 01:00 (America/Sao_Paulo), com retentativa de 3 vezes em até 1 h; o role IAM só permite `ec2:Start/StopInstances` naquela instância. As rotinas do Celery ficam dentro da janela: DF às 06:00, Senado às 07:00.
- Imagem construída localmente para `linux/amd64` e enviada com `docker save`; segredos só no `.env` da instância. SSH fechado; abre-se a 22 por IP só durante um deploy (EC2 Instance Connect).
- IMDSv2 obrigatório com limite de 1 salto (contêineres não alcançam o IMDS).

## Alternativas descartadas
Coolify em VPS; RDS (custo); Supabase como banco (limite gratuito de 500 MB frente a 8,6 GB); 24/7.

## Consequências / riscos
Custo estimado de US$ 12–14/mês. Banco de 8,6 GB e dockerd disputam 1 GB de RAM (swap obrigatório). **Sem backup automático ainda** (recomendado: snapshot diário do volume); volume sem criptografia. O `cd-main.yml` ainda dispara Coolify e não faz deploy na AWS. A coleta é interrompida às 01:00 todo dia e retomada (ver ADR 0002). O hostname gratuito do No-IP exige confirmação mensal.

# Guia de Configuração no Coolify + Cloudflare

> **Atualização (2026-10-20):** alvo de deploy superado pela EC2 agendada. Ver [ADR 0006](adr/0006-deploy-ec2-free-tier-agendado.md).

Siga este passo a passo para realizar o deploy da API em produção com domínio próprio e segurança.

## 1. Cloudflare Setup (Antes do Deploy)
1.  Aponte o registro A de `api-remunedata.com.br` para o IP da sua VM.
2.  Ative o **Proxy Laranja** (Proxied).
3.  Vá em **SSL/TLS** -> **Overview** e selecione o modo **Full (strict)**.

## 2. Criar Recurso no Coolify
1.  Crie um novo **Projeto** no Coolify.
2.  Adicione um recurso do tipo **Docker Image**.
3.  **Image Name:** `ghcr.io/seu-usuario/remunedata-api:latest`.

## 3. Configurar Variáveis (Coolify)
Na aba **Variables**, adicione:
- `DATABASE_URL`: URL do seu Postgres.
- `DATABASE_URL_SYNC`: URL do seu Postgres (para migrations).
- `REDIS_URL`: URL do seu Redis.
- `APP_ENV`: `production`
- `CORS_ORIGINS`: `["https://remunedata.com.br", "https://www.remunedata.com.br"]`
- `LOG_LEVEL`: `INFO`

## 4. Configurar Domínio no Coolify
1.  Vá em **Settings**.
2.  No campo **FQDN**, insira `https://api-remunedata.com.br`.
3.  O Coolify gerenciará o certificado interno (Traefik), enquanto a Cloudflare gerenciará o certificado externo.

## 5. Configurar Webhook para CD
1.  Vá em **Deployments** no Coolify.
2.  Copie o **Deploy Webhook URL**.
3.  No GitHub: `Settings` -> `Secrets` -> `Actions` -> Novo Segredo `COOLIFY_WEBHOOK`.

## 6. Notas de Performance (Cloudflare)
- **TTL Alto:** Como você mencionou TTL alto, lembre-se que se você fizer mudanças drásticas na API, pode precisar dar um "Purge Cache" no painel da Cloudflare.
- **Cache de Estáticos:** O dashboard em `/dashboard` será cacheado agressivamente pela Cloudflare devido ao TTL alto.

## 7. Persistência e Volumes
Para que os dados não se percam ao reiniciar os containers, você deve configurar os **Volumes** no Coolify:
1.  No recurso do **PostgreSQL**, vá na aba **Storage** (ou Volumes).
2.  Garanta que o volume `remunedata_db_prod` (ou `/var/lib/postgresql/data`) esteja mapeado para um diretório no Host (ex: `/var/lib/coolify/storage/remunedata-db`).
3.  Faça o mesmo para o **Redis** (`remunedata_redis_prod` -> `/data`).

## 8. Rede e Conectividade
1.  Certifique-se de que todos os serviços (API, Postgres, Redis, Worker, Scheduler) estejam na mesma **Docker Network** no Coolify (geralmente criada automaticamente pelo projeto).
2.  A API e o Worker usam os nomes dos serviços (`postgres`, `redis`) para se conectar. Se você mudar os nomes no Coolify, lembre-se de atualizar as variáveis de ambiente.

## 9. Estratégia de Deploy (Docker Compose)
Embora o Coolify permita deploy de imagem única, recomendamos usar o blueprint contido em [`docker-compose.prod.yml`](../docker-compose.prod.yml) como referência para orquestrar a API, o Worker e o Scheduler juntos no mesmo projeto do Coolify.

## 10. Segurança e Segredos
As senhas de produção foram geradas e estão disponíveis localmente no artefato `secrets_ready_to_use.md` (fora do controle de versão). **Nunca use as senhas do ambiente local em produção.**

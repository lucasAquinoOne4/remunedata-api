# ADR 0010 — Estados por adaptadores e proteção da carga

- **Status:** Aceito (2026-10)
- **Implementação:** `app/sources/`, `app/collector/estadual.py`, tabela `estadual_remuneracao` (migration `l3a7c9d1e5f8`)

## Contexto
Cada estado publica de um jeito (API JSON no CE, CKAN CSV em PE e SC, datastore em GO). Uma carga incompleta que substituísse o mês mostraria dados falsos como se fossem oficiais.

## Decisão
- Contrato `SourceAdapter` (`uf`, `nome`, `is_published`, `iter_rows`); uma tabela única filtrada por `uf`. Novo estado = um módulo em `app/sources/` + entrada em `MODULES`.
- **Carga protegida:** o adaptador grava em arquivo temporário fora da transação; carga com 0 linhas ou < 70% do mês já gravado é rejeitada (`LoadRejected`) e o mês anterior permanece (só `accept_shrink=True` aceita queda). Falha no meio desfaz tudo.
- Linhas idênticas legítimas (homônimos) ganham ordinal no hash e não são descartadas.
- Fonte bloqueada por WAF (GO) não é contornada; fica pausada.
- Limitação conhecida: SC publica só o bruto; CE não publica CPF nem matrícula (cruzamento só por nome).

## Consequências
Mês mais antigo nunca é apagado por uma carga ruim. Pendente: detecção explícita de WAF/HTML 200 com período de espera, e valores ausentes do SC como NULL em vez de 0,00.

# ADR 0002 — Coleta do DF serial, idempotente e retomável

- **Status:** Aceito (2026-10)
- **Implementação:** `app/core/locks.py`, `app/workers/tasks.py`, `app/collector/annual.py`, `app/persistence/repositories.py` (`save_batch`, `mark_interrupted`), migrations `d5e3f9a2b813` e `e6f4a0b3c924`

## Contexto
Incidente real: 4 coletas anuais rodando ao mesmo tempo (duas para 2025, duas para 2026) disputaram o limite do portal e gravaram o mesmo mês duas vezes (310 mil linhas, 159 mil distintas). Em outro incidente, uma trava com TTL de 6 h expirou no meio de uma coleta longa e deixou outra tarefa assumir. A "chave lógica" do doc 13 (ano, mês, identificação, matrícula, órgão) nunca foi validada.

## Decisão
- Uma coleta do DF por vez: trava Redis `lock:collect:df` com TTL de 25 h (maior que o `task_time_limit` de 24 h), dona = id da tarefa, liberada no `finally`. Quem não consegue a trava reagenda em 5 min. A trava é limpa quando o worker sobe (para não ficar órfã).
- O agendamento diário (`sync_recent_years_task`) não empilha tarefas se já há coleta rodando, e enfileira o ano atual antes do anterior.
- Meses do mais recente para o mais antigo (`TRANSPARENCIA_RECENT_MONTHS_FIRST`).
- Mês "success" **com dados** é pulado; mês "success" com **0 registros** (portal ainda não publicou) é tentado de novo. Sem isso, um mês publicado depois nunca entraria.
- Retomada por `paginas_consumidas`; o `delete_records_by_period` só roda quando o mês começa do zero.
- Duplicatas: índice único `(ano_exercicio, mes_referencia, md5(raw_payload_json))` e `INSERT ... ON CONFLICT DO NOTHING`, fatiado em 500 linhas (36 colunas x 500 < 32767 parâmetros do asyncpg).
- Na subida do worker, execuções que ficaram `running` (instância desligada no meio) são marcadas como interrompidas: mês vira `error`, ano vira `partial_success` ou `error`.
- Índice de cobertura `ix_remuneration_summary (ano, nome_orgao) INCLUDE (valor_bruto)` e `count(*)` no `get_summary`: o dashboard vira *index-only scan* (de mais de 120 s para poucos segundos numa t3.micro).
- As migrations criam os índices com `CONCURRENTLY` (fora de transação); a de unicidade remove antes as cópias exatas.

## Alternativas descartadas
Chave lógica do doc 13; limpar e recoletar sempre; deduplicar só no código; trava com TTL curto e heartbeat (mais peças móveis).

## Consequências
Reexecutar é seguro. Um ano novo espera a fila. O índice funcional sobre `md5` do JSON é caro em disco e escrita. O worker limpa a trava ao subir: com mais de um worker isso liberaria a trava de uma coleta viva (hoje há um só processo de worker).

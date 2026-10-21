# ADR 0009 — Desempenho do portal, cache em Redis e carga só para admin

- **Status:** Aceito (2026-10)
- **Implementação:** `app/services/people.py`, `app/api/endpoints/remuneration.py` e `admin.py`, migrations `f7a1c3d9e5b2`, `h9c3e5f7a2b4`, `i0d4f6a8b3c5`

## Contexto
Com 4,7 milhões de linhas do DF, a busca e os gráficos eram lentos: busca por nome comum até 4,7 s, lista de competências 9,8 s, gráficos 20 s, perfil 3,2 s. O histórico da pessoa era por CPF mascarado (sem índice e misturando pessoas). Além disso, as rotas de execução (inclusive `POST /executions/`, que dispara coleta) eram acessíveis a qualquer assinante, e a listagem de execuções era pública.

## Decisão
- **Retrato por pessoa (`person_latest`, migration `j1e5a7b9c3d6`):** uma linha por pessoa e fonte com a competência mais recente (~790 mil linhas, 271 MB com índices). A busca por nome, as sugestões e o cruzamento entre fontes usam essa tabela; ela é reconstruída quando a carga da competência mais recente de uma fonte termina. Motivo: na instância de 1 GB o índice trigram das tabelas mensais (> 1 GB, com a mesma pessoa repetida por mês) não cabe em memória e a busca "maria silva" no DF levou 62 s e os gráficos 143 s. A busca só usa o retrato se ele existir (senão cai nas tabelas mensais) e se a competência pedida for a dele ou não for informada.
- **Pré-aquecimento:** tarefa `warm_portal_cache_task` às 05:50 (a instância liga às 05:30 e o Redis não persiste) calcula competências e gráficos das três fontes.
- **Consultas indexadas e limitadas:** índice trigram sobre `immutable_unaccent(nome)` (ADR 0007), índices de cobertura por competência e por cargo, contagem limitada a 10.000 (`total_capped`), página ordenada só entre as primeiras 3.000 candidatas.
- **Competências do DF** vêm de `execution_monthly` (tabela pequena); **gráficos mensais** vêm de `period_stat`, calculado mês a mês sob demanda e invalidado quando a carga do mês termina (os 2 meses mais recentes são recalculados a cada 15 min).
- **Cache Redis** nos endpoints: busca 5 min, sugestões 10 min, perfil 10 min, estatísticas 1 h, competências 6 h, ficha federal 7 dias, filtros 24 h. Qualquer carga concluída limpa o prefixo `remuneration:`.
- **Perfil enriquecido** (`/remuneration/person`): histórico por identificador da fonte (não por CPF), todos os componentes de valor, comparação com pares (mesmo cargo na mesma competência: média, mediana, p90, percentil), cruzamento **probabilístico** com as outras fontes (`provavel`: nome + 6 dígitos centrais do CPF; `possivel`: só nome; CPF central diferente descarta) e ficha da API federal.
- **Pesquisa dinâmica:** `/suggest` e `/periods`, e a tela busca enquanto se digita (debounce de 250 ms, abas por fonte em paralelo).
- **Carga só para admin:** `executions` (listar, detalhar, disparar, retentar) e as rotas novas `{admin}/loads` exigem admin; o export CSV/XLSX continua sendo recurso de assinante. A tela "Carga de dados" vive na área admin e o item some do menu para os demais.
- A exportação da busca (`/remuneration/export`, CSV até 5.000 e XLSX até 1.000 linhas) é recurso de assinante.

## Consequências
Medido (cache frio, dados locais): competências 0,03 s, busca por nome 0,1 s (retrato), perfil 0,2 s, gráficos 1 s (depois de calculados, instantâneos pelo cache). Mais índices (disco e escrita) e mais uma tabela. A busca de nomes muito comuns ordena só entre as 3.000 primeiras candidatas; o usuário refina.

# ADR 0009 — Desempenho do portal, cache em Redis e carga só para admin

- **Status:** Aceito (2026-10)
- **Implementação:** `app/services/people.py`, `app/api/endpoints/remuneration.py` e `admin.py`, migrations `f7a1c3d9e5b2`, `h9c3e5f7a2b4`, `i0d4f6a8b3c5`

## Contexto
Com 4,7 milhões de linhas do DF, a busca e os gráficos eram lentos: busca por nome comum até 4,7 s, lista de competências 9,8 s, gráficos 20 s, perfil 3,2 s. O histórico da pessoa era por CPF mascarado (sem índice e misturando pessoas). Além disso, as rotas de execução (inclusive `POST /executions/`, que dispara coleta) eram acessíveis a qualquer assinante, e a listagem de execuções era pública.

## Decisão
- **Consultas indexadas e limitadas:** índice trigram sobre `immutable_unaccent(nome)` (ADR 0007), índices de cobertura por competência e por cargo, contagem limitada a 10.000 (`total_capped`), página ordenada só entre as primeiras 3.000 candidatas.
- **Competências do DF** vêm de `execution_monthly` (tabela pequena); **gráficos mensais** vêm de `period_stat`, calculado mês a mês sob demanda e invalidado quando a carga do mês termina (os 2 meses mais recentes são recalculados a cada 15 min).
- **Cache Redis** nos endpoints: busca 5 min, sugestões 10 min, perfil 10 min, estatísticas 1 h, competências 6 h, ficha federal 7 dias, filtros 24 h. Qualquer carga concluída limpa o prefixo `remuneration:`.
- **Perfil enriquecido** (`/remuneration/person`): histórico por identificador da fonte (não por CPF), todos os componentes de valor, comparação com pares (mesmo cargo na mesma competência: média, mediana, p90, percentil), cruzamento **probabilístico** com as outras fontes (`provavel`: nome + 6 dígitos centrais do CPF; `possivel`: só nome; CPF central diferente descarta) e ficha da API federal.
- **Pesquisa dinâmica:** `/suggest` e `/periods`, e a tela busca enquanto se digita (debounce de 250 ms, abas por fonte em paralelo).
- **Carga só para admin:** `executions` (listar, detalhar, disparar, retentar) e as rotas novas `{admin}/loads` exigem admin; o export CSV/XLSX continua sendo recurso de assinante. A tela "Carga de dados" vive na área admin e o item some do menu para os demais.
- A exportação da busca (`/remuneration/export`, CSV até 5.000 e XLSX até 1.000 linhas) é recurso de assinante.

## Consequências
Medido (cache frio, dados locais): competências 0,03 s, busca "silva" 0,3 s, perfil 0,2 s, gráficos 1 s (depois de calculados, instantâneos pelo cache). Mais índices (disco e escrita) e mais uma tabela. A busca de nomes muito comuns ordena só entre as 3.000 primeiras candidatas; o usuário refina.

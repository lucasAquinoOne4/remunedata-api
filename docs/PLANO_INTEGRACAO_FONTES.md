# Plano de integração de novas fontes de remuneração

Levantamento feito em 13/10/2026 (GETs públicos, sem conta, sem captcha). "Testado" = endpoint chamado e resposta vista.
Base: [estudo Transparência Brasil/República.org, abr/2026](https://backend.transparencia.org.br/wp-content/uploads/2026/04/transparencia_remuneracoes_subnacionais.pdf).

## Estado atual

| Fonte | Situação | Tabela |
|---|---|---|
| DF | Integrado (API paginada, `x-client-id`) | `remuneration_collected` |
| Senado Federal | Integrado (1 requisição por mês, junção por nome) | `senado_remuneracao` |

## Fontes candidatas (ordem recomendada)

| # | Ente | Acesso | Identificador de pessoa | Defasagem | Dificuldade |
|---|---|---|---|---|---|
| 1 | CE | API REST JSON, 100/página, sem chave (testado) | nenhum (só `id` do registro) | ~1 mês | Baixa |
| 2 | PE | CKAN, CSV mensal ~20–76 MB (testado) | CPF mascarado + matrícula | ~1 mês | Baixa |
| 3 | GO | CKAN com datastore paginável, série 2012+ (testado) | nenhum | ~1 mês | Baixa |
| 4 | SC | CKAN, CSV/JSON mensal (testado) | CPF mascarado; só valor bruto | ~1 semana | Baixa |
| 5 | MG | CKAN, CSV ~130 MB/mês (testado) | `masp` + admissão; CPF vazio | ~6 meses | Baixa |
| 6 | SP | CSV do mês corrente + ZIP histórico (testado) | CPF mascarado | 2–3 meses | Média |
| 7 | BA | ZIPs de 682 MB; nome mascarado (parcial) | matrícula + CPF parcial | ~1 mês | Média |
| 8 | ES | não verificado (rede bloqueada daqui) | — | — | Média (estimada) |
| 9 | PR | portal JSF com POST/ViewState (não testado) | — | — | Média |
| 10 | RS | Power BI, sem URL de arquivo | — | — | Alta |
| 11 | MS | SPA com gateway interno e token | — | — | Alta (usar LAI) |
| 12 | MT | reCAPTCHA v3 | — | — | Alta (só LAI) |
| 13 | RJ | consulta só por CPF/nome, WAF bloqueou o IP | — | — | Inviável (só LAI) |

Detalhes de URLs, campos e armadilhas por estado estão no relatório da pesquisa (ver seção "Armadilhas conhecidas").

## Arquitetura proposta

1. **Contrato de adaptador** (`app/sources/base.py`): cada fonte implementa `fetch_month(ano, mes) -> Iterable[RegistroNormalizado]`, mais `disponivel(ano, mes)`. O adaptador cuida de paginação, formato (JSON/CSV/ZIP) e parsing; o resto do sistema não conhece a fonte.
2. **Registro normalizado**: `fonte` (ex.: `CE`, `PE`), `ano`, `mes`, `nome`, `cargo`, `orgao`, `situacao`, `valor_bruto`, `valor_liquido`, `identificador_pessoa` (opcional, mascarado), `raw_payload_json`. O que não for comum fica no payload bruto.
3. **Persistência**: uma tabela única `remuneracao_estadual` para os novos entes, com `UNIQUE (fonte, ano, mes, chave_registro)` e índice trigram em `upper(nome)`. DF e Senado ficam como estão, para não arriscar o que já funciona; uma unificação posterior é opcional.
4. **Coleta idempotente por mês** (padrão do Senado): `replace_month(fonte, ano, mes)`. Tarefa Celery por fonte e agendamento diário revisitando os últimos N meses. Mês vazio é "ainda não publicado", não erro.
5. **Download em streaming** para CSV/ZIP grandes (MG, BA, PE): gravar em disco temporário e inserir em lotes (COPY ou `executemany` de 5 a 10 mil linhas), nunca carregar o arquivo inteiro na memória.
6. **API/Frontend**: `fonte` passa a aceitar os códigos de UF; a lista de fontes vem de um endpoint (`/remuneration/fontes`) para o seletor não ficar fixo no código.
7. **Config**: `.env` + Pydantic (`<UF>_API_BASE_URL`, timeouts, meses de sincronização); nada fixo no código.

## Fases

- **Fase 0 (feita):** DF + Senado, `fonte` na API e seletor no frontend.
- **Fase 1:** contrato de adaptador + tabela normalizada + **CE** (encaixe mais direto: API paginada). Valida a arquitetura.
- **Fase 2:** **PE** e **SC** (CKAN/CSV; PE traz matrícula e CPF mascarado). Introduz o download em streaming.
- **Fase 3:** **GO** (datastore), **MG** (arquivos grandes, User-Agent de navegador), **SP** (carimbar a competência na coleta).
- **Fase 4:** **BA** (se fizer sentido com nome mascarado), reteste de **ES** de outra rede, **PR** (avaliar sessão JSF).
- **Fora de escopo técnico:** RS, MS, MT, RJ. Via correta: pedido de acesso à informação (LAI).

Cada fase só entra com testes em `tests/` (banco `*_test`) e validação manual de uma amostra contra o portal de origem.

## Armadilhas conhecidas

- **CE:** sem CPF/matrícula; chave natural nome + órgão + cargo, com risco de homônimos. ~1.925 requisições por mês (~1,3 h a 2,5 s/chamada).
- **PE:** mês só no nome do arquivo/recurso; recursos duplicados em alguns meses; separador `;`.
- **GO:** sem identificador pessoal; `ANOMES` vem nas colunas.
- **SC:** só valor bruto; colunas sem mês/ano (vem do nome do arquivo).
- **MG:** `curl` sem User-Agent devolve corpo vazio; `datastore_search` não funciona nos meses recentes; mês no formato `32026`; último arquivo é de 03/2026.
- **SP:** HEAD retorna 405 (usar GET); o arquivo "corrente" muda todo mês, então registrar a competência no momento do download; números em formato BR e nomes com espaços à direita.
- **BA:** nome mascarado impede cruzamento por nome; valores estão em outro arquivo do ZIP (não inspecionado).
- **Senado (lição):** `sequencial` não é chave comum entre folha e cadastro. Sempre validar chaves de junção antes de confiar nelas.

## Identidade e LGPD

- Nenhuma fonte aberta entrega CPF completo. Chave de pessoa será composta (fonte + órgão + nome + cargo, e matrícula/CPF mascarado quando existir), aceitando homônimos. Resultados de cruzamento entre fontes são **prováveis**, nunca confirmados.
- O STF (Tema 483) permite a divulgação nominal de remunerações. Mesmo assim, documentar finalidade e base legal, e respeitar mascaramentos da fonte (BA).
- Licenças explícitas só vistas em MG (CC-BY-4.0), SC e PE (Creative Commons Atribuição). Conferir o termo de uso de cada portal antes de publicar dados.
- Não contornar captcha, WAF ou sessão. Para MT, RJ, MS e RS, usar LAI.

## Riscos operacionais

- Limite de taxa dos portais (o DF respondeu 429): manter pausa entre requisições e tratamento de `Retry-After`.
- Tamanho: PE/MG/BA/GO somam centenas de MB por mês; planejar disco e tempo de carga.
- Os testes precisam rodar sempre em banco `*_test` (a suíte faz TRUNCATE).

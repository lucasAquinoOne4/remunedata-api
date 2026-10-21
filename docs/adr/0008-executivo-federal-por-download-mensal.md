# ADR 0008 — Executivo federal por download mensal em lote

- **Status:** Aceito (2026-10)
- **Implementação:** `app/infra/federal_client.py`, `app/collector/federal.py`, `FederalRepository`, tabela `federal_remuneracao` (migration `g8b2d4e6f1a3`), `?fonte=FEDERAL`

## Contexto
O Portal da Transparência federal (CGU) tem uma API com chave (header `chave-api-dados`), mas a remuneração é **por servidor** (`/servidores/remuneracao?id=&mesAno=`): centenas de milhares de chamadas por mês. O mesmo portal publica, sem chave, o ZIP mensal `AAAAMM_Servidores_SIAPE` (~83 MB) com `Cadastro.csv` e `Remuneracao.csv` do mês inteiro. Os dados mudam pouco: uma carga por mês basta.

## Decisão
- Fonte de dados: o **ZIP mensal** (um download por mês), não a API por servidor. Mês não publicado responde 302 e depois 403 (XML); publicado responde 200 (`application/x-zip-compressed`). Um `HEAD` decide.
- Junção por `Id_SERVIDOR_PORTAL`, chave comum real entre Cadastro e Remuneração (ao contrário do Senado). Os arquivos não vêm ordenados e o cadastro repete o id (vários vínculos): a junção usa um SQLite temporário em disco (memória constante no worker de 300 MB) e o primeiro cadastro de cada id vence. Remuneração sem cadastro é gravada com os campos de cadastro nulos.
- Cabeçalhos são localizados por nome normalizado (sem acento). Coluna ausente aborta com erro claro (o layout do portal mudou).
- Idempotente (`replace_month`, numa transação) e **sem tráfego quando nada muda**: a verificação é **semanal** (quarta, 08:00), considera só os últimos 2 meses (`FEDERAL_SYNC_MONTHS`) e um mês já carregado é pulado sem nenhuma requisição ao portal.
- `valor_bruto` = remuneração básica + abate-teto + gratificação natalina + abate-teto da natalina + férias + outras eventuais (verbas indenizatórias ficam separadas); `valor_liquido` = remuneração após deduções obrigatórias. Convenção nossa, a validar contra o portal.
- Não guarda o payload bruto (centenas de milhares de linhas por mês). CPF e matrícula ficam mascarados como vêm.
- A API com chave é usada **sob demanda**, uma chamada por pessoa aberta no perfil (`/servidores/{id}`, o mesmo id do ZIP, conferido), com cache de 7 dias. A chave (`PORTAL_FEDERAL_API_KEY`) fica só no `.env`.
- Medido: 516.754 registros de 08/2026 em 70 s.

## Alternativas descartadas
API por servidor para a carga (volume e limite de 400 req/min); consulta ao vivo no portal a cada busca.

## Consequências
~500 mil linhas por mês no banco (disco da instância limita a poucos meses). O ZIP só cobre civis do SIAPE: militares e outros órgãos ficam fora. Cruzamento com o DF por nome + 6 dígitos centrais do CPF (ambos mascaram assim).

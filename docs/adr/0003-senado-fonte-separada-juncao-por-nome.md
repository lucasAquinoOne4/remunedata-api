# ADR 0003 — Senado Federal como fonte separada, com junção por nome

- **Status:** Aceito (2026-10)
- **Implementação:** `app/infra/senado_client.py`, `app/collector/senado.py`, `SenadoRepository`, tabela `senado_remuneracao` (migration `c4d2e8f1a702`), parâmetro `?fonte=SENADO`

## Contexto
O Senado publica a folha em `adm.senado.gov.br/adm-dadosabertos/api/v1`: uma resposta por mês (~11 mil registros), sem paginação nem chave de API, e o cadastro separado (~23 mil). Não há CPF.

## Decisão
- Tabela própria, coleta mensal idempotente (`replace_month`: apaga e insere o mês numa transação), últimos 3 meses sincronizados às 07:00.
- A folha é unida ao cadastro por **nome normalizado** (sem acento, caixa alta), e só quando o nome é único tanto no cadastro quanto na folha do mês (contando `sequencial` distinto: a mesma pessoa em folha Normal e Suplementar não é homônimo). Homônimos ficam sem cargo/lotação.
- O `sequencial` **não** é chave comum entre os endpoints: em 1.611 casos o número coincide mas pertence a outra pessoa (verificado). Uma junção inicial por `sequencial` atribuiu cargos errados e foi descartada.
- Cadastro com menos de 1.000 registros ou resposta que não seja lista aborta a coleta, em vez de gravar o mês sem enriquecimento por cima do anterior.
- `valor_bruto` = soma de proventos (básica, vantagens, função, natalina, horas extras, eventuais, abono, indenizatórias, diárias, auxílios); previdência, IR, faltas e reversão do teto são deduções. É uma convenção nossa, a validar contra o portal.
- Valores: formato BR `5.612,35`; texto inválido, NaN e infinito viram 0 com aviso; o ano e o mês gravados são os da competência pedida.

## Alternativas descartadas
Junção por `sequencial`; atribuir cargo ao homônimo; tabela única normalizada já (adiada, ver `docs/PLANO_INTEGRACAO_FONTES.md`).

## Consequências
Cargo/lotação do cadastro é o estado **atual**, então meses antigos refeitos recebem o cargo de hoje. Sem CPF, o cruzamento com outras fontes é só provável. Uma tabela por fonte até o contrato de adaptadores da Fase 1 do plano.

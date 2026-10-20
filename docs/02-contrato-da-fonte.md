# 02 - Contrato da fonte

> **Atualização (2026-10-20):** o portal passou a exigir o header `x-client-id` e a limitar a taxa por IP (429 com `Retry-After` real). O `X-BROWSER-VERSION` deste documento não é mais suficiente. Ver [ADR 0001](adr/0001-convivencia-com-o-portal-do-df.md).

## Objetivo
Documentar como a fonte de dados de remuneração do Portal da Transparência do DF é acessada, quais parâmetros usa e quais informações principais retorna.

## Endpoint principal
- Método: `GET`
- URL base: `https://www.transparencia.df.gov.br/api/remuneracao`

## Exemplo de chamada observada

    GET /api/remuneracao?anoExercicio=2025&busy=true&dataAtualizacao=2026-03-17T13:49:35.173Z&editing=true&icon=md-search&mesReferencia=06&nomeServidor=&padrao=false&page=0&size=500&tipo=csv HTTP/1.1
    Host: www.transparencia.df.gov.br
    Accept: */*
    X-BROWSER-VERSION: fb265b45-2d92-41d1-a6f3-00182e878d96

## Resumo do comportamento observado
- O endpoint responde com `200 OK`
- O `Content-Type` retornado é `application/json;charset=UTF-8`
- A resposta é paginada
- A resposta contém:
  - lista de registros em `content`
  - metadados de paginação
  - totalizador agregado com `valorLiquido` e `valorBruto`

## Query params identificados

| Parâmetro | Exemplo | Classificação inicial | Observação |
|---|---|---|---|
| `anoExercicio` | `2025` | necessário | define o ano da consulta |
| `mesReferencia` | `06` | necessário | define o mês da competência |
| `page` | `0` | necessário | índice da página |
| `size` | `500` | parcialmente necessário | controla o tamanho solicitado, mas a API retornou `pageSize=150`, indicando possível limite interno |
| `nomeServidor` | vazio | opcional | filtro por nome do servidor |
| `tipo` | `csv` | incerto | mesmo com esse valor, a resposta veio em JSON |
| `busy` | `true` | provavelmente desnecessário | aparenta ser parâmetro de interface |
| `editing` | `true` | provavelmente desnecessário | aparenta ser parâmetro de interface |
| `icon` | `md-search` | provavelmente desnecessário | aparenta ser parâmetro visual do front-end |
| `padrao` | `false` | incerto | pode ser controle de comportamento da interface |
| `dataAtualizacao` | `2026-03-17T13:49:35.173Z` | provavelmente desnecessário | aparenta ser valor de contexto da tela, não da consulta |

## Hipótese inicial sobre parâmetros realmente úteis para coleta
A coleta automatizada provavelmente dependerá apenas de:

- `anoExercicio`
- `mesReferencia`
- `page`
- `size`
- `nomeServidor` (quando houver filtro)

Os demais parâmetros aparentam estar ligados ao front-end e devem ser validados futuramente com testes removendo-os da requisição.

## Estrutura da resposta

### Estrutura de alto nível
A resposta contém os seguintes blocos principais:

- `content`
- `pageable`
- `totalizador`
- `totalPages`
- `last`
- `totalElements`
- `numberOfElements`
- `sort`
- `first`
- `size`
- `number`

### Exemplo dos metadados de paginação

    {
      "pageable": {
        "sort": null,
        "pageNumber": 0,
        "pageSize": 150,
        "offset": 0
      },
      "totalizador": {
        "valorLiquido": 2752543542.32,
        "valorBruto": 3465501251.34
      },
      "totalPages": 1728,
      "last": false,
      "totalElements": 259053,
      "numberOfElements": 150,
      "sort": null,
      "first": true,
      "size": 150,
      "number": 0
    }

## Estrutura principal de cada item retornado

### Identificação
- `codigoOrgaoArtificial`
- `codigoIdentificacao`
- `codigoMatricula`
- `cpfServidor`
- `nomeServidor`

### Referência temporal
- `anoExercicio`
- `mesReferencia`

### Lotação / vínculo
- `codigoOrgao`
- `nomeOrgao`
- `situacaoFuncional`
- `situacaoFuncionalDetalhada`
- `cargo`
- `funcao`

### Valores financeiros
- `valorRemuneracaoBasica`
- `valorBeneficios`
- `valorFuncoes`
- `valorComissaoConselheiro`
- `valorHoraExtra`
- `valorVerbasEventuais`
- `valorVerbasJudiciais`
- `valorReceitasMesesAnteriores`
- `valorReposicaoDescontosMaior`
- `valorLicencaPremio`
- `valorImpostoRenda`
- `valorSeguridadeSocial`
- `valorRedutorTeto`
- `valorDescontosMesesAnteriores`
- `valorReposicaoPagamentoMaior`
- `valorExpurgo`
- `valorLiquido`
- `valorBruto`

## Exemplo simplificado de um item

    {
      "codigoOrgaoArtificial": "0100802",
      "codigoIdentificacao": "37412615",
      "codigoMatricula": "70488525",
      "cpfServidor": "***927115**",
      "anoExercicio": 2025,
      "mesReferencia": 6,
      "nomeServidor": " ADILA DE JESUS VIEIRA                            ",
      "codigoOrgao": "802",
      "nomeOrgao": "SECRETARIA DE ESTADO DE EDUCACAO - TEMPORARIO                                   ",
      "situacaoFuncional": "ATIVO",
      "situacaoFuncionalDetalhada": "ATIVO                         ",
      "cargo": "CONTRATO TEMPORARIO           ",
      "funcao": "                                                            ",
      "valorRemuneracaoBasica": 6352.64,
      "valorBeneficios": 640.00,
      "valorFuncoes": 0.00,
      "valorHoraExtra": 0.00,
      "valorVerbasEventuais": 0.00,
      "valorVerbasJudiciais": 0.00,
      "valorImpostoRenda": 621.39,
      "valorSeguridadeSocial": 788.55,
      "valorLiquido": 5582.70,
      "valorBruto": 6992.64
    }

## Observações importantes da fonte

### 1. A resposta é JSON, mesmo com `tipo=csv`
Apesar do parâmetro `tipo=csv`, a API respondeu com:
- `Content-Type: application/json;charset=UTF-8`

Isso indica que:
- `tipo=csv` não força exportação CSV nessa chamada observada; ou
- o front-end usa esse parâmetro sem que o backend altere o formato da resposta; ou
- existe outro fluxo específico para exportação real

### 2. Existe paginação real
A resposta confirma paginação com:
- `totalPages: 1728`
- `totalElements: 259053`
- `number: 0`
- `first: true`
- `last: false`

### 3. O tamanho real retornado foi menor que o solicitado
A requisição usou:
- `size=500`

Mas a resposta devolveu:
- `pageSize: 150`
- `numberOfElements: 150`
- `size: 150`

Hipótese inicial:
- a API possui limite interno de 150 registros por página, ignorando valores acima disso

### 4. Há totalizador agregado no retorno
A resposta inclui:

    {
      "totalizador": {
        "valorLiquido": 2752543542.32,
        "valorBruto": 3465501251.34
      }
    }

Isso pode ser útil futuramente para:
- conferência de integridade da coleta
- validação de completude por página/mês
- estatísticas ou dashboard

## Campos que provavelmente deverão ser persistidos
Campos mínimos que valem a pena persistir de forma estruturada:

- `codigoIdentificacao`
- `codigoMatricula`
- `cpfServidor`
- `nomeServidor`
- `anoExercicio`
- `mesReferencia`
- `codigoOrgao`
- `nomeOrgao`
- `situacaoFuncional`
- `situacaoFuncionalDetalhada`
- `cargo`
- `funcao`
- `valorRemuneracaoBasica`
- `valorBeneficios`
- `valorFuncoes`
- `valorHoraExtra`
- `valorVerbasEventuais`
- `valorVerbasJudiciais`
- `valorReceitasMesesAnteriores`
- `valorReposicaoDescontosMaior`
- `valorLicencaPremio`
- `valorImpostoRenda`
- `valorSeguridadeSocial`
- `valorRedutorTeto`
- `valorDescontosMesesAnteriores`
- `valorReposicaoPagamentoMaior`
- `valorExpurgo`
- `valorLiquido`
- `valorBruto`

## Campos que também podem ser mantidos como payload bruto
Além dos campos estruturados, vale armazenar:
- o item completo em JSON bruto (`raw_payload`)
- os metadados da consulta:
  - ano
  - mês
  - página
  - tamanho solicitado
  - total de elementos
  - total de páginas

## Chave lógica inicial candidata
Ainda sem fechar modelagem definitiva, uma chave lógica inicial possível para evitar duplicidade pode combinar:

- `codigoIdentificacao`
- `codigoMatricula`
- `anoExercicio`
- `mesReferencia`
- `codigoOrgao`

Essa hipótese ainda precisa ser validada com mais páginas e casos duplicados.

## Dúvidas em aberto
- Quais parâmetros além de `anoExercicio`, `mesReferencia`, `page`, `size` e `nomeServidor` são realmente necessários?
- `tipo=csv` tem algum efeito real em outro fluxo?
- A API sempre limita `size` para `150`?
- O parâmetro `nomeServidor` aceita busca parcial, exata ou ambos?
- Existe ordenação padrão fixa?
- Existe endpoint separado para detalhe individual do servidor?
- O campo `codigoIdentificacao` é estável entre competências diferentes?
- A combinação `codigoIdentificacao + codigoMatricula` identifica unicamente o servidor no tempo?
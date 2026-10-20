# 05 - Validação do limite de size

> **Atualização (2026-10-20):** `size=150` deixou de ser hipótese: é o teto confirmado (o servidor devolve 150 mesmo se pedirem 500) e está fixado em configuração. Ver [ADR 0001](adr/0001-convivencia-com-o-portal-do-df.md).

## Objetivo
Validar qual é o limite real do parâmetro `size` na API de remuneração.

## Consulta base
- `anoExercicio`: `2025`
- `mesReferencia`: `06`
- `page`: `0`
- `nomeServidor`: vazio

## Teste 1 - size 5
- URL: `https://www.transparencia.df.gov.br/api/remuneracao?anoExercicio=2025&mesReferencia=06&page=0&size=5&nomeServidor=`
- Status code: `200`
- Evidência observada: `RawContentLength = 5559`
- Interpretação: a API respondeu corretamente com um lote pequeno.
- Observações: comportamento compatível com retorno reduzido de registros.

## Teste 2 - size 10
- URL: `https://www.transparencia.df.gov.br/api/remuneracao?anoExercicio=2025&mesReferencia=06&page=0&size=10&nomeServidor=`
- Status code: `200`
- Evidência observada: `RawContentLength = 10863`
- Interpretação: a API respondeu corretamente com mais dados do que no teste com `size=5`.
- Observações: isso indica que a API respeita valores menores de `size`.

## Teste 3 - size 150
- URL: `https://www.transparencia.df.gov.br/api/remuneracao?anoExercicio=2025&mesReferencia=06&page=0&size=150&nomeServidor=`
- Status code: `200`
- Evidência observada: `RawContentLength = 159392`
- Interpretação: a API respondeu com um lote muito maior, compatível com o provável limite máximo por página.
- Observações: este teste representa o maior retorno observado até aqui.

## Teste 4 - size 500
- URL: `https://www.transparencia.df.gov.br/api/remuneracao?anoExercicio=2025&mesReferencia=06&page=0&size=500&nomeServidor=`
- Status code: `200`
- Evidência observada: `RawContentLength = 159392`
- Interpretação: o retorno teve exatamente o mesmo tamanho do teste com `size=150`.
- Observações: isso indica fortemente que a API limita internamente o tamanho máximo da página e ignora valores acima desse teto.

## Comparação dos resultados
- `size=5` -> `RawContentLength = 5559`
- `size=10` -> `RawContentLength = 10863`
- `size=150` -> `RawContentLength = 159392`
- `size=500` -> `RawContentLength = 159392`

## Conclusão
A API respeitou valores menores de `size`, como `5` e `10`, retornando respostas proporcionais ao aumento solicitado. No entanto, os testes com `size=150` e `size=500` produziram exatamente o mesmo tamanho de resposta, o que indica fortemente que existe um teto real interno. A hipótese mais provável, com base nos testes realizados, é que o limite efetivo por página seja `150` registros.

## Limitações desta validação
- Nesta etapa, a validação foi feita por comparação do tamanho bruto da resposta (`RawContentLength`), e não pela contagem explícita dos itens do array `content`.
- Ainda vale, em uma etapa futura, confirmar no corpo JSON os campos:
  - `numberOfElements`
  - `size`
  - `pageable.pageSize`

## Resultado prático para o projeto
Até nova evidência, o coletor deve assumir:
- `size=150` como valor máximo efetivo por página.

## Dúvidas em aberto
- Confirmar explicitamente no JSON se `pageable.pageSize` retorna `150`.
- Confirmar explicitamente se `numberOfElements` retorna `150` quando `size=500`.
- Verificar se esse limite de `150` é estável para outros meses e anos.
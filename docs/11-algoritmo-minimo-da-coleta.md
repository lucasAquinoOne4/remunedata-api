# 11 - Algoritmo mínimo da coleta

> **Atualização (2026-10-20):** o algoritmo real difere deste mínimo: meses do mais recente para o mais antigo, uma coleta do DF por vez (trava Redis), tratamento de 429 com bloqueio global, mês vazio é reprocessado e a gravação ignora duplicatas. Ver [ADR 0001](adr/0001-convivencia-com-o-portal-do-df.md) e [ADR 0002](adr/0002-coleta-serial-idempotente-e-retomavel.md).

## Objetivo
Definir a regra mínima de funcionamento do coletor com base nas validações realizadas na API.

## Entrada do processo
- Parâmetro principal: `anoExercicio`
- Estratégia inicial: para um ano informado, iterar os meses de `01` a `12`

## Unidade real de coleta
A unidade real de coleta da API é a competência mensal, definida por:
- `anoExercicio`
- `mesReferencia`

Portanto, coletar um ano significa executar 12 coletas mensais e consolidar os resultados.

## Estratégia de paginação
Para cada mês:
1. iniciar em `page=0`
2. usar `size=150`
3. consultar a API
4. processar os registros retornados
5. avançar para a próxima página
6. repetir até atingir a condição de parada

## Condições de parada da paginação
A coleta de páginas deve ser encerrada quando qualquer uma das condições abaixo ocorrer:
- `last = true`
- `numberOfElements = 0`
- `content.Count = 0`

## Regras já validadas
- a API responde fora do navegador com chamada HTTP simples
- a paginação funciona
- a ordenação padrão mostrou estabilidade básica
- o tamanho máximo efetivo por página é `150`
- o filtro por nome funciona
- o filtro por CPF não foi validado como funcional
- a coleta anual deve ser feita mês a mês

## Dados mínimos a registrar por execução
Para cada competência mensal coletada, registrar:
- ano
- mês
- página
- quantidade de registros retornados
- totalPages informado pela API
- totalElements informado pela API
- horário de início
- horário de fim
- status da coleta
- mensagem de erro, se houver

## Fluxo mínimo do coletor
1. receber o ano desejado
2. iterar os meses de `01` a `12`
3. para cada mês, iniciar a coleta na página `0`
4. consumir a API usando `size=150`
5. salvar/processar os registros da página atual
6. verificar a condição de parada
7. se não parar, avançar para a próxima página
8. ao finalizar o mês, registrar o resumo da competência
9. ao finalizar os 12 meses, registrar o resumo anual

## Riscos já identificados
- a API não evidenciou filtro funcional por CPF
- valores de `size` acima de `150` não trazem ganho prático
- a coleta anual depende de consolidar corretamente 12 competências mensais

## Conclusão
As validações realizadas já permitem definir um algoritmo mínimo confiável para o coletor, mesmo antes da implementação em código.
# 13 - Modelo mínimo de persistência

> **Atualização (2026-10-20):** a chave lógica candidata abaixo foi descartada. A deduplicação é um índice único `(ano, mês, md5(raw_payload_json))`; há também a tabela `senado_remuneracao` e índices de cobertura e de busca por nome. Ver [ADR 0002](adr/0002-coleta-serial-idempotente-e-retomavel.md), [ADR 0003](adr/0003-senado-fonte-separada-juncao-por-nome.md) e [ADR 0007](adr/0007-busca-por-nome-com-indice-trigram.md).

## Objetivo
Definir o conjunto mínimo de estruturas de persistência necessárias para registrar a execução do coletor e armazenar os dados coletados.

## Visão geral
Na primeira versão, o sistema precisa persistir três grupos de informação:

1. a execução anual do processo
2. a execução de cada competência mensal
3. os registros de remuneração coletados

## Estrutura 1 - execução anual
Representa a execução principal do coletor para um determinado ano.

Campos mínimos:
- `id`
- `ano_exercicio`
- `status`
- `started_at`
- `finished_at`
- `duration_ms`
- `total_meses_processados`
- `total_paginas_consumidas`
- `total_registros_coletados`
- `error_message`

## Estrutura 2 - execução mensal
Representa a execução de uma competência mensal dentro da execução anual.

Campos mínimos:
- `id`
- `execution_id`
- `mes_referencia`
- `status`
- `paginas_consumidas`
- `registros_coletados`
- `total_pages_informado`
- `total_elements_informado`
- `started_at`
- `finished_at`
- `error_message`

## Estrutura 3 - remuneração coletada
Representa cada registro retornado pela API para uma competência mensal.

Campos mínimos:
- `id`
- `execution_id`
- `monthly_execution_id`
- `ano_exercicio`
- `mes_referencia`
- `codigo_identificacao`
- `codigo_matricula`
- `cpf_servidor`
- `nome_servidor`
- `codigo_orgao`
- `nome_orgao`
- `situacao_funcional`
- `situacao_funcional_detalhada`
- `cargo`
- `funcao`
- `valor_remuneracao_basica`
- `valor_beneficios`
- `valor_funcoes`
- `valor_comissao_conselheiro`
- `valor_hora_extra`
- `valor_verbas_eventuais`
- `valor_verbas_judiciais`
- `valor_receitas_meses_anteriores`
- `valor_reposicao_descontos_maior`
- `valor_licenca_premio`
- `valor_imposto_renda`
- `valor_seguridade_social`
- `valor_redutor_teto`
- `valor_descontos_meses_anteriores`
- `valor_reposicao_pagamento_maior`
- `valor_expurgo`
- `valor_liquido`
- `valor_bruto`
- `raw_payload_json`

## Relações mínimas
- uma execução anual possui várias execuções mensais
- uma execução mensal possui vários registros de remuneração
- cada registro de remuneração pertence a uma execução anual e a uma execução mensal

## Chave lógica inicial candidata
Uma hipótese inicial de chave lógica para evitar duplicidade é a combinação de:

- `ano_exercicio`
- `mes_referencia`
- `codigo_identificacao`
- `codigo_matricula`
- `codigo_orgao`

Essa hipótese ainda deve ser validada com mais cuidado durante a implementação.

## Observações
- na primeira versão, a prioridade é rastreabilidade e simplicidade
- normalizações mais sofisticadas podem ser feitas depois
- o campo `raw_payload_json` ajuda em auditoria, depuração e reprocessamento

## Conclusão
Com esse modelo mínimo, o projeto já consegue registrar a execução do coletor e armazenar os dados necessários para consultas futuras e auditoria básica.
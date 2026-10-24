# ADR 0013 — Participação societária (dados abertos de CNPJ)

- **Status:** Aceito (2026-10)
- **Implementação:** `app/services/rfb.py`, `scripts/rfb_etl.py` (filtro local), migration `o6d0f2a4b8c3`, `refresh_rfb_task`

## Contexto
A Receita Federal publica o quadro de sócios e os cadastros de empresas (dados abertos de CNPJ). Os arquivos têm dezenas de GB e trazem dados pessoais (endereço, telefone, e-mail, CPF parcial), muito além do necessário. O ADR 0012 adiou este item por exigir decisão de minimização.

## Decisão
- **Filtro local, fora do banco:** o ETL lê os arquivos da Receita na máquina de quem opera e mantém só as sociedades de pessoas que casam com as nossas (nome idêntico normalizado + 6 dígitos centrais do CPF), gerando CSVs pequenos. A API só carrega esses CSVs (`python -m app.services.rfb DIR` ou `refresh_rfb_task`).
- **Regra de cruzamento:** como no TSE, exige nome idêntico E os 6 dígitos centrais do CPF (confiança "provável"). Nome sozinho não cruza; Senado e fontes sem CPF não cruzam.
- **O que NÃO é guardado:** CPF completo, endereço, telefone, e-mail, sócios que não casam com nossas pessoas. Guardam-se `cpf_core`, nome, qualificação, data de entrada, faixa etária e dados cadastrais da empresa (razão social, natureza, capital, porte, situação, CNAE, UF).
- **Carga atômica:** valida cabeçalhos e hashes do manifest, recusa diretório sem sócios ou sem empresas, descarta `cpf_core` fora de 6 dígitos e substitui o conteúdo anterior numa única transação. A competência fica em `rfb_carga`.
- **Perfil:** chave `participacoes_societarias` (lista, `[]` se nada), com falha isolada para nunca derrubar o perfil.

## Consequências
- **Ser sócio não indica irregularidade.** A legislação costuma vedar apenas a administração de empresa (e, em regimes de dedicação exclusiva, atividade empresarial); a base não distingue sócio-administrador de cotista em todos os casos nem informa a dedicação do servidor. A interface deve apresentar o dado como informação, nunca como acusação.
- Homônimos com os mesmos 6 dígitos centrais são raros, mas possíveis: por isso a confiança é "provável".
- A base muda todo mês e é carregada manualmente; o dado exibido reflete a competência de `rfb_carga`.

## Adendo (2026-10-24): carga real e filtro de casos atípicos
A primeira carga completa (CNPJ de 2026-10) casou 296.844 sócios em 250.643 empresas, com 197.636 pessoas distintas. Uma pessoa aparecia em 2.219 empresas (a segunda maior tinha 51), o que indica cadastro em massa ou caso atípico. Decisão: `scripts/rfb_filtrar_outliers.py` remove da carga quem tem mais de 100 participações, registra a contagem no `manifest.json` (`filtro_pos_etl`) e o perfil nunca exibe essas pessoas. Motivo: atribuir algo grave a quem não tem relação é pior que omitir um caso extremo.

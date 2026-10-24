# ADR 0012 — Enriquecimento com TSE e Justiça (DadosJusBR)

- **Status:** Aceito (2026-10)
- **Implementação:** `app/services/tse.py`, `app/sources/ju.py`, migration `n5c9e1f3a7b2`

## Contexto
A pesquisa de fontes (especialistas → auditores → validadores → decisão) priorizou o que é público, sem chave, sem captcha e juridicamente seguro. O TSE publica candidaturas e bens declarados; o DadosJusBR padroniza contracheques de tribunais e MPs.

## Decisão
- **TSE (`refresh_tse_task`, manual, eleições 2022 e 2024):** a base traz CPF completo e e-mail; **só os 6 dígitos centrais do CPF são gravados**, o resto é descartado na carga. O perfil só mostra candidaturas e patrimônio quando **nome idêntico E os 6 dígitos centrais batem** (confiança "provável"). Nome sozinho não cruza, porque atribuir patrimônio ao homônimo errado causa dano real. Fontes sem CPF (Senado) não cruzam. Host fixo (`cdn.tse.jus.br`) e teto de tamanho no download.
- **Justiça (`JU`):** adaptador do framework de estados para TJDFT, MPDFT e TRE-DF (configurável em `DADOSJUSBR_ORGAOS`), via API e ZIPs públicos do DadosJusBR, com lista fixa de hosts. Não há CPF, então o cruzamento é só por nome ("possível"). Meses marcados `inconsistente` pela origem são carregados com sinalização.
- **Adiados:** CNPJ/sócios (Receita), CAPES, PNCP, SIORG, CEAF, viagens a serviço (o host da CGU responde 405; não será contornado), CEIS/CNEP: exigem decisão jurídica ou acesso que não temos.

## Consequências
Medido localmente: 568 servidores federais, 305 do DF, 193 de PE e 122 de SC cruzam com candidaturas; JU carregou ~990 registros/mês. Pendente: interface para `candidaturas_tse` e para a fonte JU.

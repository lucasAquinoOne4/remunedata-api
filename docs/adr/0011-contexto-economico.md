# ADR 0011 — Contexto econômico (IPCA, salário mínimo, teto)

- **Status:** Aceito (2026-10)
- **Implementação:** `app/services/economia.py`, migration `m4b8d0e2f6a1`

## Decisão
Cada mês do perfil mostra o valor em reais constantes (IPCA, IBGE SIDRA 1737), em salários mínimos (BCB SGS 1619) e como % do teto constitucional (Lei 14.520/2023). Três requisições por atualização mensal; dados agregados, sem dado pessoal. Meses futuros do salário mínimo são descartados.

## Consequências
O teto é referência, não acusação: verbas indenizatórias podem legitimamente exceder o teto, e a interface deve dizer isso. Os valores do teto vêm de migration com a norma citada e precisam de nova linha a cada reajuste.

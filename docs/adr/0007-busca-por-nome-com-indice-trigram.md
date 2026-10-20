# ADR 0007 — Busca por nome com índice trigram

- **Status:** Aceito (2026-10)
- **Implementação:** migration `f7a1c3d9e5b2`, `RemunerationRepository.search`, `SenadoRepository.search`

## Contexto
A busca por nome era `unaccent(nome) ILIKE unaccent('%x%')`: varredura completa. Com 4,7 milhões de linhas e 8,6 GB, leva 7,6 s num servidor local e seria inviável na instância pequena. O índice trigram inicial do Senado (`upper(nome)`) nunca era usado, porque a consulta não usava essa expressão.

## Decisão
- Função `immutable_unaccent(text)` (wrapper IMMUTABLE de `unaccent`, que é STABLE e não indexável) e índices GIN `gin_trgm_ops` sobre ela, nas duas tabelas, criados com `CONCURRENTLY`.
- O termo é normalizado em Python (sem acento) e os caracteres `%`, `_` e `\` digitados pelo usuário são escapados: a busca é literal (antes `%` casava com tudo).
- Resultado medido: 7,6 s para 0,07 s (4,7 milhões de linhas).

## Consequências
Mais disco (índice GIN sobre 4,7 milhões de nomes) e escrita um pouco mais lenta. Exige as extensões `pg_trgm` e `unaccent` (permissão de criação de extensão; em RDS gerenciado depende do papel).

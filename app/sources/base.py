"""
app/sources/base.py
Contrato dos adaptadores de remuneração de ESTADOS (CE, PE, GO, SC, ...).

Cada estado publica os dados de um jeito (API paginada, CSV, CKAN, ZIP). O adaptador esconde isso e
entrega linhas NORMALIZADAS; o resto do sistema (coletor, tabela, busca, perfil, cruzamento) é
comum. Ver docs/PLANO_INTEGRACAO_FONTES.md e o ADR 0010.
"""

import re
from pathlib import Path
from typing import Any, Dict, Iterator, Optional, Protocol

import httpx


class SourceBlocked(RuntimeError):
    """A fonte nos bloqueou (401/403, WAF ou HTML no lugar de dados). Não se insiste nem se contorna:
    o coletor entra em espera (cooldown) e tenta de novo mais tarde."""


class SourceIncomplete(RuntimeError):
    """A fonte entregou menos registros do que ela mesma declara; a carga não deve ser gravada."""


def _check(response: httpx.Response) -> None:
    if response.status_code in (401, 403):
        raise SourceBlocked(f"HTTP {response.status_code} em {response.request.url.host}")
    ctype = response.headers.get("content-type", "").lower()
    if response.status_code == 200 and "text/html" in ctype:
        raise SourceBlocked(f"HTML no lugar de dados em {response.request.url.host} (possível WAF)")


def block_guard(response: httpx.Response) -> None:
    _check(response)


async def async_block_guard(response: httpx.Response) -> None:
    _check(response)


SYNC_HOOKS = {"response": [block_guard]}
ASYNC_HOOKS = {"response": [async_block_guard]}

# CPF só pode chegar mascarado: no máximo 6 dígitos visíveis (ex.: '***.236.123-**').
_MASKED_CPF = re.compile(r"^[\d.*\-\s]+$")


def safe_masked_cpf(value: Any) -> Optional[str]:
    """Devolve o CPF só se estiver mascarado (≤ 6 dígitos); qualquer coisa que pareça CPF completo vira None."""
    text = str(value or "").strip()
    if not text or not _MASKED_CPF.match(text):
        return None
    return text if sum(c.isdigit() for c in text) <= 6 else None

# Campos de uma linha normalizada (todos opcionais, exceto nome e ident):
#   ident          chave ESTÁVEL da pessoa entre meses (matrícula, ou md5 de nome|órgão quando a
#                  fonte não traz identificador). Obrigatório.
#   nome           nome do servidor. Obrigatório.
#   cpf_mascarado  como a fonte publica (ex.: '***.236.123-**'); nunca CPF completo
#   matricula, cargo, orgao, situacao, vinculo
#   valor_bruto, valor_liquido, valor_descontos   (R$, float)
#   detalhes       dict {chave_snake_case: valor} com os demais componentes numéricos da fonte
NormalRow = Dict[str, Any]


class SourceAdapter(Protocol):
    """Uma fonte estadual."""

    uf: str  # sigla (ex.: 'CE'); é o valor do parâmetro ?fonte=
    nome: str  # rótulo para a interface (ex.: 'Governo do Ceará')

    async def is_published(self, ano: int, mes: int) -> bool:
        """O mês já está disponível na fonte? Deve custar poucas requisições (HEAD/1 página)."""

    def iter_rows(self, ano: int, mes: int, work_dir: Path) -> Iterator[NormalRow]:
        """
        Linhas normalizadas do mês. Pode baixar para `work_dir` (apagado depois). É síncrono (CSV
        em disco) ou um gerador que chama a API respeitando limite de taxa.
        """


def normalize_row(row: NormalRow) -> Optional[NormalRow]:
    """Valida o mínimo de uma linha normalizada; devolve None para linha inutilizável."""
    if not row.get("nome") or not row.get("ident"):
        return None
    return row

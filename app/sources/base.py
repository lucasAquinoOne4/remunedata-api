"""
app/sources/base.py
Contrato dos adaptadores de remuneração de ESTADOS (CE, PE, GO, SC, ...).

Cada estado publica os dados de um jeito (API paginada, CSV, CKAN, ZIP). O adaptador esconde isso e
entrega linhas NORMALIZADAS; o resto do sistema (coletor, tabela, busca, perfil, cruzamento) é
comum. Ver docs/PLANO_INTEGRACAO_FONTES.md e o ADR 0010.
"""

from pathlib import Path
from typing import Any, Dict, Iterator, Optional, Protocol

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

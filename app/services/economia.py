"""
app/services/economia.py
Contexto econômico para a remuneração: IPCA (índice, IBGE SIDRA 1737), salário mínimo (BCB SGS
1619) e Selic mensal (BCB SGS 4390), mais o teto constitucional. Serve para mostrar o valor em REAIS
CONSTANTES, em SALÁRIOS MÍNIMOS e como % do teto. Dados agregados, sem dado pessoal.
"""

from datetime import date, datetime
from typing import Dict, List, Optional, Tuple

import httpx
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.persistence.models import IndiceEconomico, TetoConstitucional

logger = get_logger(__name__)

SERIE_IPCA_INDICE = "ipca_indice"
SERIE_SALARIO_MINIMO = "salario_minimo"
SERIE_SELIC_MES = "selic_mes"

_BCB = "https://api.bcb.gov.br/dados/serie/bcdata.sgs.{code}/dados?formato=json&dataInicial={ini}&dataFinal={fim}"
_SIDRA_IPCA = "https://apisidra.ibge.gov.br/values/t/1737/n1/all/v/2266/p/all?formato=json"


def _parse_bcb(rows: list) -> List[Tuple[int, int, float]]:
    out = []
    for item in rows:
        try:
            d = datetime.strptime(item["data"], "%d/%m/%Y")
            out.append((d.year, d.month, float(item["valor"])))
        except (KeyError, ValueError, TypeError):
            continue
    return out


def _parse_sidra(rows: list) -> List[Tuple[int, int, float]]:
    out = []
    for item in rows[1:]:  # a 1ª linha é o cabeçalho
        try:
            code = str(item["D3C"])  # AAAAMM
            out.append((int(code[:4]), int(code[4:6]), float(item["V"])))
        except (KeyError, ValueError, TypeError):
            continue
    return out


async def fetch_series(today: Optional[date] = None) -> Dict[str, List[Tuple[int, int, float]]]:
    """Baixa as séries (1 requisição por série). O BCB devolve meses FUTUROS do salário mínimo
    (valor repetido, não realizado): descartados."""
    today = today or date.today()
    ini = "01/01/2018"
    fim = f"31/12/{today.year}"
    async with httpx.AsyncClient(timeout=60, headers={"Accept": "application/json"}) as client:
        sm = (await client.get(_BCB.format(code=1619, ini=ini, fim=fim))).json()
        selic = (await client.get(_BCB.format(code=4390, ini=ini, fim=fim))).json()
        ipca = (await client.get(_SIDRA_IPCA)).json()
    limite = (today.year, today.month)
    return {
        SERIE_SALARIO_MINIMO: [r for r in _parse_bcb(sm) if (r[0], r[1]) <= limite],
        SERIE_SELIC_MES: [r for r in _parse_bcb(selic) if (r[0], r[1]) <= limite],
        SERIE_IPCA_INDICE: _parse_sidra(ipca),
    }


async def store_series(
    session: AsyncSession, series: Dict[str, List[Tuple[int, int, float]]]
) -> int:
    total = 0
    for serie, points in series.items():
        for start in range(0, len(points), 500):
            chunk = [
                {"serie": serie, "ano": a, "mes": m, "valor": v}
                for a, m, v in points[start : start + 500]
            ]
            stmt = pg_insert(IndiceEconomico).values(chunk)
            await session.execute(
                stmt.on_conflict_do_update(
                    index_elements=["serie", "ano", "mes"], set_={"valor": stmt.excluded.valor}
                )
            )
            total += len(chunk)
    await session.commit()
    return total


async def refresh(session: AsyncSession) -> int:
    series = await fetch_series()
    count = await store_series(session, series)
    logger.info(f"Contexto econômico atualizado: {count} pontos.")
    return count


class Contexto:
    """Séries em memória para enriquecer um histórico de remuneração."""

    def __init__(
        self, ipca: Dict[Tuple[int, int], float], sm: Dict[Tuple[int, int], float], tetos: list
    ):
        self.ipca, self.sm, self.tetos = ipca, sm, sorted(tetos)  # tetos: (data_inicio, valor)
        self.referencia = max(ipca) if ipca else None  # mês mais recente com índice

    def teto_em(self, ano: int, mes: int) -> Optional[float]:
        vigente = None
        for inicio, valor in self.tetos:
            if (inicio.year, inicio.month) <= (ano, mes):
                vigente = valor
        return vigente

    def enriquecer(self, ano: int, mes: int, bruto: float) -> Dict[str, Optional[float]]:
        """bruto em reais do mês de referência (inflação), em salários mínimos e % do teto."""
        out: Dict[str, Optional[float]] = {
            "bruto_real": None,
            "salarios_minimos": None,
            "teto": None,
            "pct_teto": None,
        }
        idx_mes, idx_ref = (
            self.ipca.get((ano, mes)),
            self.ipca.get(self.referencia) if self.referencia else None,
        )
        if idx_mes and idx_ref:
            out["bruto_real"] = round(bruto * idx_ref / idx_mes, 2)
        sm = self.sm.get((ano, mes))
        if sm:
            out["salarios_minimos"] = round(bruto / sm, 2)
        teto = self.teto_em(ano, mes)
        if teto:
            out["teto"] = teto
            out["pct_teto"] = round(100.0 * bruto / teto, 1)
        return out


async def carregar_contexto(session: AsyncSession) -> Contexto:
    rows = (await session.execute(select(IndiceEconomico))).scalars().all()
    ipca = {(r.ano, r.mes): r.valor for r in rows if r.serie == SERIE_IPCA_INDICE}
    sm = {(r.ano, r.mes): r.valor for r in rows if r.serie == SERIE_SALARIO_MINIMO}
    tetos = [
        (t.vigencia_inicio, t.valor)
        for t in (await session.execute(select(TetoConstitucional))).scalars().all()
    ]
    return Contexto(ipca, sm, tetos)

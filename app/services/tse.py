"""
app/services/tse.py
Enriquecimento com dados abertos do TSE: candidaturas e patrimônio declarado (bens).

LGPD: a base do TSE traz o CPF completo e o e-mail do candidato. Aqui só se grava o recorte de 6
dígitos centrais do CPF (o mesmo que as fontes de servidores mascaram) e nada de e-mail. O cruzamento
com um servidor exige nome idêntico E os 6 dígitos centrais: nome sozinho NÃO basta, porque atribuir
patrimônio ao homônimo errado causaria dano real.
"""

import csv
import re
import zipfile
from collections import defaultdict
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Tuple
from urllib.parse import urlparse

import httpx
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.persistence.models import TseBem, TseCandidato

logger = get_logger(__name__)

_HOST = "cdn.tse.jus.br"
_BASE = "https://cdn.tse.jus.br/estatistica/sead/odsele"
_MAX_ZIP_BYTES = 250 * 1024 * 1024
_CHUNK = 2000


def _url(kind: str, ano: int) -> str:
    return f"{_BASE}/{kind}/{kind}_{ano}.zip"


def download(kind: str, ano: int, dest: Path) -> Path:
    """Baixa um ZIP do TSE em disco (stream), só do host oficial e com teto de tamanho."""
    url = _url(kind, ano)
    if urlparse(url).hostname != _HOST:
        raise ValueError("host não permitido")
    out = dest / f"{kind}_{ano}.zip"
    size = 0
    headers = {"User-Agent": "Mozilla/5.0 (RemuneData)"}
    with httpx.stream("GET", url, headers=headers, timeout=120, follow_redirects=False) as r:
        r.raise_for_status()
        with out.open("wb") as fh:
            for part in r.iter_bytes(1 << 20):
                size += len(part)
                if size > _MAX_ZIP_BYTES:
                    raise ValueError(f"{url}: maior que o limite de {_MAX_ZIP_BYTES} bytes")
                fh.write(part)
    return out


def cpf_core_from_full(cpf: Optional[str]) -> Optional[str]:
    """Dígitos 4 a 9 do CPF completo (o recorte que as fontes de servidores publicam)."""
    digits = re.sub(r"\D", "", cpf or "")
    return digits[3:9] if len(digits) == 11 else None


def _money(text: Optional[str]) -> float:
    try:
        return float((text or "0").strip().replace(".", "").replace(",", "."))
    except ValueError:
        return 0.0


def _csv_files(zf: zipfile.ZipFile, prefix: str) -> List[str]:
    names = [n for n in zf.namelist() if n.lower().endswith(".csv") and Path(n).name.startswith(prefix)]
    brasil = [n for n in names if n.upper().endswith("_BRASIL.CSV")]
    if brasil:
        return brasil
    return [n for n in names if re.search(r"_[A-Z]{2}\.csv$", n, re.I) and not n.upper().endswith("_BR.CSV")]


def _read_csv(zf: zipfile.ZipFile, name: str) -> Iterator[Dict[str, str]]:
    with zf.open(name) as raw:
        import io

        text = io.TextIOWrapper(raw, encoding="latin-1", newline="")
        yield from csv.DictReader(text, delimiter=";", quotechar='"')


def _clean(v: Optional[str]) -> Optional[str]:
    v = (v or "").strip()
    return None if v in ("", "#NULO", "#NE", "NÃO DIVULGÁVEL", "-1", "-3") else v


def parse_candidatos(zip_path: Path, ano: int) -> Iterator[dict]:
    from app.services.people import norm_name

    seen = set()
    with zipfile.ZipFile(zip_path) as zf:
        for name in _csv_files(zf, "consulta_cand_"):
            for r in _read_csv(zf, name):
                sq = int(r["SQ_CANDIDATO"])
                if sq in seen or not _clean(r.get("NM_CANDIDATO")):
                    continue
                seen.add(sq)
                nome = r["NM_CANDIDATO"].strip()[:255]
                yield {
                    "ano_eleicao": ano,
                    "sq_candidato": sq,
                    "nome": nome,
                    "nome_norm": norm_name(nome)[:255],
                    "cpf_core": cpf_core_from_full(r.get("NR_CPF_CANDIDATO")),
                    "uf": (_clean(r.get("SG_UF")) or "")[:2] or None,
                    "cargo": (_clean(r.get("DS_CARGO")) or None),
                    "partido": _clean(r.get("SG_PARTIDO")),
                    "ocupacao": _clean(r.get("DS_OCUPACAO")),
                    "situacao": _clean(r.get("DS_SITUACAO_CANDIDATURA")),
                    "resultado": _clean(r.get("DS_SIT_TOT_TURNO")),
                }


def parse_bens(zip_path: Path, ano: int) -> Iterator[dict]:
    agg: Dict[Tuple[int, str], List[float]] = defaultdict(lambda: [0, 0.0])
    with zipfile.ZipFile(zip_path) as zf:
        for name in _csv_files(zf, "bem_candidato_"):
            for r in _read_csv(zf, name):
                tipo = (_clean(r.get("DS_TIPO_BEM_CANDIDATO")) or "Outros")[:100]
                key = (int(r["SQ_CANDIDATO"]), tipo)
                agg[key][0] += 1
                agg[key][1] += _money(r.get("VR_BEM_CANDIDATO"))
    for (sq, tipo), (qtd, valor) in agg.items():
        yield {"ano_eleicao": ano, "sq_candidato": sq, "tipo": tipo, "quantidade": int(qtd), "valor": valor}


async def _bulk(session: AsyncSession, table, rows: Iterator[dict]) -> int:
    total, batch = 0, []
    for row in rows:
        batch.append(row)
        if len(batch) >= _CHUNK:
            await session.execute(pg_insert(table).values(batch).on_conflict_do_nothing())
            total += len(batch)
            batch = []
    if batch:
        await session.execute(pg_insert(table).values(batch).on_conflict_do_nothing())
        total += len(batch)
    return total


async def load_year(session: AsyncSession, ano: int, cand_zip: Path, bem_zip: Path) -> Dict[str, int]:
    """Substitui a eleição `ano` (idempotente), numa transação; falha desfaz tudo."""
    try:
        await session.execute(delete(TseBem).where(TseBem.ano_eleicao == ano))
        await session.execute(delete(TseCandidato).where(TseCandidato.ano_eleicao == ano))
        n_cand = await _bulk(session, TseCandidato.__table__, parse_candidatos(cand_zip, ano))
        n_bem = await _bulk(session, TseBem.__table__, parse_bens(bem_zip, ano))
        if n_cand == 0:
            raise ValueError(f"TSE {ano}: 0 candidaturas; carga descartada")
        await session.commit()
    except BaseException:
        await session.rollback()
        raise
    logger.info(f"TSE {ano}: {n_cand} candidaturas, {n_bem} grupos de bens.")
    return {"ano": ano, "candidaturas": n_cand, "bens": n_bem}


async def lookup(session: AsyncSession, nome: str, cpf_core: Optional[str]) -> List[dict]:
    """Candidaturas do mesmo nome E mesmos 6 dígitos centrais do CPF (mais recentes primeiro)."""
    from app.services.people import norm_name

    if not cpf_core or not nome:
        return []
    cands = (
        await session.execute(
            select(TseCandidato)
            .where(TseCandidato.cpf_core == cpf_core, TseCandidato.nome_norm == norm_name(nome))
            .order_by(TseCandidato.ano_eleicao.desc())
            .limit(10)
        )
    ).scalars().all()
    out = []
    for c in cands:
        bens = (
            await session.execute(
                select(TseBem.tipo, TseBem.quantidade, TseBem.valor)
                .where(TseBem.ano_eleicao == c.ano_eleicao, TseBem.sq_candidato == c.sq_candidato)
                .order_by(TseBem.valor.desc())
            )
        ).all()
        out.append(
            {
                "ano_eleicao": c.ano_eleicao,
                "cargo": c.cargo,
                "uf": c.uf,
                "partido": c.partido,
                "ocupacao": c.ocupacao,
                "situacao": c.situacao,
                "resultado": c.resultado,
                "patrimonio_declarado": round(sum(b[2] for b in bens), 2),
                "bens": [{"tipo": b[0], "quantidade": b[1], "valor": round(b[2], 2)} for b in bens],
                "confianca": "provavel",
                "motivo": "mesmo nome e mesmos 6 dígitos centrais do CPF",
            }
        )
    return out

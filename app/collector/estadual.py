"""
app/collector/estadual.py
Carga mensal de remuneração de um ESTADO a partir de um adaptador de fonte (app/sources).

Idempotente (reexecutar o mês substitui os registros dele). Um mês já carregado é pulado sem nenhuma
requisição à fonte. As linhas do adaptador são normalizadas, deduplicadas por hash e gravadas em
lotes numa única transação.
"""

import hashlib
import json
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterator

from app.core.logging import get_logger
from app.persistence.repositories import EstadualRepository
from app.services.people import invalidate_period_stat
from app.sources.base import NormalRow, SourceAdapter, normalize_row

logger = get_logger(__name__)

_NUMERIC = ("valor_bruto", "valor_liquido", "valor_descontos")


def _prepare(uf: str, ano: int, mes: int, rows: Iterator[NormalRow]) -> Iterator[Dict[str, Any]]:
    """Normaliza, completa e deduplica as linhas do adaptador para o INSERT."""
    seen = set()
    for raw in rows:
        row = normalize_row(raw)
        if row is None:
            continue
        payload = {k: row.get(k) for k in sorted(row)}
        digest = hashlib.md5(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()
        if digest in seen:
            continue
        seen.add(digest)
        out: Dict[str, Any] = {
            "uf": uf,
            "ano": ano,
            "mes": mes,
            "linha_hash": digest,
            "ident": str(row["ident"])[:40],
            "nome": str(row["nome"]).strip()[:255],
            "cpf_mascarado": (row.get("cpf_mascarado") or None),
            "matricula": (str(row["matricula"])[:40] if row.get("matricula") else None),
            "cargo": (str(row["cargo"])[:255] if row.get("cargo") else None),
            "orgao": (str(row["orgao"])[:255] if row.get("orgao") else None),
            "situacao": (str(row["situacao"])[:100] if row.get("situacao") else None),
            "vinculo": (str(row["vinculo"])[:100] if row.get("vinculo") else None),
            "detalhes": row.get("detalhes") or None,
        }
        for key in _NUMERIC:
            out[key] = float(row.get(key) or 0.0)
        yield out


class EstadualCollector:
    """Carrega um mês de um estado."""

    def __init__(self, adapter: SourceAdapter, repo: EstadualRepository):
        self.adapter = adapter
        self.repo = repo

    async def collect_month(self, ano: int, mes: int, force: bool = False) -> Dict[str, Any]:
        uf = self.adapter.uf
        base = {"uf": uf, "ano": ano, "mes": mes}
        if not force and await self.repo.month_loaded(ano, mes):
            return {**base, "registros": 0, "status": "already_loaded"}
        if not await self.adapter.is_published(ano, mes):
            logger.info(f"{uf} {mes:02d}/{ano}: ainda não publicado.")
            return {**base, "registros": 0, "status": "empty"}

        with tempfile.TemporaryDirectory(prefix=f"{uf.lower()}-") as tmp:
            rows = self.adapter.iter_rows(ano, mes, Path(tmp))
            total = await self.repo.replace_month(ano, mes, _prepare(uf, ano, mes, rows))
        try:
            await invalidate_period_stat(self.repo.session, uf, ano, mes)
        except Exception as exc:  # agregados e retrato são cache: nunca derrubam a carga
            logger.warning(f"Não foi possível atualizar agregados de {uf} {mes:02d}/{ano}: {exc}")
        logger.info(f"{uf} {mes:02d}/{ano}: {total} registros gravados.")
        return {**base, "registros": total, "status": "success"}

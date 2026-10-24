"""
app/collector/estadual.py
Carga mensal de remuneração de um ESTADO a partir de um adaptador de fonte (app/sources).

Idempotente (reexecutar o mês substitui os registros dele). Um mês já carregado é pulado sem nenhuma
requisição à fonte. As linhas do adaptador são normalizadas, deduplicadas por hash e gravadas em
lotes numa única transação.
"""

import asyncio
import hashlib
import json
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterator

from app.core import locks
from app.core.logging import get_logger
from app.persistence.repositories import EstadualRepository
from app.services.people import invalidate_period_stat
from app.sources.base import NormalRow, SourceAdapter, SourceBlocked, normalize_row, safe_masked_cpf

logger = get_logger(__name__)

_NUMERIC = ("valor_bruto", "valor_liquido", "valor_descontos")
# Carga com menos que esta fração do mês anterior é tratada como truncada e NÃO substitui o mês.
MIN_RATIO_VS_PREVIOUS = 0.7
BLOCK_COOLDOWN_SECONDS = 6 * 3600


class LoadRejected(Exception):
    """Carga descartada por parecer incompleta (vazia ou muito menor que a anterior); o mês antigo é mantido."""


def _prepare(uf: str, ano: int, mes: int, rows: Iterator[NormalRow]) -> Iterator[Dict[str, Any]]:
    """Normaliza, completa e deduplica as linhas do adaptador para o INSERT."""
    seen: Dict[str, int] = {}
    for raw in rows:
        row = normalize_row(raw)
        if row is None:
            continue
        payload = {k: row.get(k) for k in sorted(row)}
        digest = hashlib.md5(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()
        # Linhas idênticas legítimas (homônimos com mesmo cargo/valor) ganham ordinal: não são descartadas.
        ordinal = seen.get(digest, 0)
        seen[digest] = ordinal + 1
        if ordinal:
            digest = hashlib.md5(f"{digest}#{ordinal}".encode()).hexdigest()
        out: Dict[str, Any] = {
            "uf": uf,
            "ano": ano,
            "mes": mes,
            "linha_hash": digest,
            "ident": str(row["ident"])[:40],
            "nome": str(row["nome"]).strip()[:255],
            "cpf_mascarado": safe_masked_cpf(row.get("cpf_mascarado")),
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

    def _spool(self, uf: str, ano: int, mes: int, work_dir: Path, spool: Path) -> int:
        count = 0
        with spool.open("w", encoding="utf-8") as fh:
            for row in _prepare(uf, ano, mes, self.adapter.iter_rows(ano, mes, work_dir)):
                fh.write(json.dumps(row, default=str) + "\n")
                count += 1
        return count

    @staticmethod
    def _read_spool(spool: Path) -> Iterator[Dict[str, Any]]:
        with spool.open(encoding="utf-8") as fh:
            for line in fh:
                yield json.loads(line)

    def __init__(self, adapter: SourceAdapter, repo: EstadualRepository):
        self.adapter = adapter
        self.repo = repo

    async def collect_month(
        self, ano: int, mes: int, force: bool = False, accept_shrink: bool = False
    ) -> Dict[str, Any]:
        uf = self.adapter.uf
        base = {"uf": uf, "ano": ano, "mes": mes}
        if not force and await self.repo.month_loaded(ano, mes):
            return {**base, "registros": 0, "status": "already_loaded"}
        wait = await asyncio.to_thread(locks.blocked_remaining, uf)
        if wait > 0:
            logger.info(f"{uf}: fonte em espera por bloqueio ({wait}s); nada foi pedido.")
            return {**base, "registros": 0, "status": "blocked"}
        try:
            return await self._collect(ano, mes, force, accept_shrink, base)
        except SourceBlocked as exc:
            await asyncio.to_thread(locks.set_blocked, uf, BLOCK_COOLDOWN_SECONDS)
            logger.warning(f"{uf} {mes:02d}/{ano}: fonte bloqueou o acesso ({exc}); espera de 6 h.")
            return {**base, "registros": 0, "status": "blocked"}

    async def _collect(
        self, ano: int, mes: int, force: bool, accept_shrink: bool, base: Dict[str, Any]
    ) -> Dict[str, Any]:
        uf = self.adapter.uf
        if not await self.adapter.is_published(ano, mes):
            logger.info(f"{uf} {mes:02d}/{ano}: ainda não publicado.")
            return {**base, "registros": 0, "status": "empty"}

        with tempfile.TemporaryDirectory(prefix=f"{uf.lower()}-") as tmp:
            spool = Path(tmp) / "spool.jsonl"
            # Baixa/parseia e grava em disco FORA da transação e do event loop; o banco só é tocado
            # depois, numa transação curta (a fonte pode levar horas, ex.: CE).
            count = await asyncio.to_thread(self._spool, uf, ano, mes, Path(tmp), spool)
            previous = await self.repo.month_count(ano, mes)
            if count == 0:
                raise LoadRejected(f"{uf} {mes:02d}/{ano}: fonte devolveu 0 linhas; mês anterior mantido.")
            if previous and count < previous * MIN_RATIO_VS_PREVIOUS and not accept_shrink:
                raise LoadRejected(
                    f"{uf} {mes:02d}/{ano}: {count} linhas contra {previous} já gravadas "
                    f"(< {int(MIN_RATIO_VS_PREVIOUS * 100)}%); mês mantido. Use accept_shrink para aceitar."
                )
            total = await self.repo.replace_month(ano, mes, self._read_spool(spool))
        try:
            await invalidate_period_stat(self.repo.session, uf, ano, mes)
        except Exception as exc:  # agregados e retrato são cache: nunca derrubam a carga
            logger.warning(f"Não foi possível atualizar agregados de {uf} {mes:02d}/{ano}: {exc}")
        logger.info(f"{uf} {mes:02d}/{ano}: {total} registros gravados.")
        return {**base, "registros": total, "status": "success"}

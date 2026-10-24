"""
app/services/rfb.py
Participação societária a partir dos dados abertos de CNPJ da Receita Federal.

O filtro pesado é feito fora (scripts/rfb_etl.py): só chegam aqui as sociedades de pessoas que casam com
as nossas (nome idêntico normalizado + 6 dígitos centrais do CPF). LGPD / minimização: guarda-se apenas
o recorte de 6 dígitos do CPF; nunca CPF completo, endereço, telefone ou e-mail. O cruzamento com um
servidor exige nome idêntico E os 6 dígitos centrais (confiança "provável"); nome sozinho NÃO cruza.

Uso em produção:  python -m app.services.rfb /caminho/do/diretorio
"""

import asyncio
import csv
import hashlib
import json
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional

from sqlalchemy import case, delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.core.logging import get_logger
from app.persistence.models import RfbCarga, RfbDominio, RfbEmpresa, RfbSocio

logger = get_logger(__name__)

_CHUNK = 2000

SOCIOS_COLS = ["cnpj_basico", "nome_socio", "nome_norm", "cpf_core", "qualificacao_cod", "data_entrada", "faixa_etaria"]
EMPRESAS_COLS = [
    "cnpj_basico", "razao_social", "natureza_juridica_cod", "capital_social", "porte_cod",
    "situacao_cadastral_cod", "data_situacao", "data_inicio_atividade", "cnae_principal", "uf",
    "municipio_cod", "nome_fantasia",
]
DOMINIO_COLS = ["cod", "descricao"]

# Domínios estáticos (não vêm em arquivo da Receita no formato que consumimos)
PORTES = {"00": "Não informado", "01": "Microempresa", "03": "Empresa de pequeno porte", "05": "Demais"}
SITUACOES = {"01": "Nula", "02": "Ativa", "03": "Suspensa", "04": "Inapta", "08": "Baixada"}
_SITUACAO_ATIVA = "02"


# ───────────────────────── leitura ─────────────────────────


def _open_csv(path: Path, colunas: List[str]) -> Iterator[Dict[str, str]]:
    """Lê o CSV em fluxo, exigindo cabeçalho exatamente igual ao contrato."""
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        header = [h.strip() for h in (reader.fieldnames or [])]
        faltam = [c for c in colunas if c not in header]
        sobram = [c for c in header if c not in colunas]
        if faltam or sobram:
            raise ValueError(
                f"{path.name}: cabeçalho inválido (faltam: {faltam or '-'}; inesperadas: {sobram or '-'})"
            )
        for row in reader:
            yield {k.strip(): (v or "").strip() for k, v in row.items() if k is not None}


def _txt(v: Optional[str], n: int) -> Optional[str]:
    v = (v or "").strip()
    return v[:n] if v else None


def _date(v: Optional[str]) -> Optional[date]:
    try:
        return date.fromisoformat((v or "").strip()) if (v or "").strip() else None
    except ValueError:
        return None


def _float(v: Optional[str]) -> Optional[float]:
    try:
        return float(v) if (v or "").strip() else None
    except ValueError:
        return None


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for part in iter(lambda: fh.read(1 << 20), b""):
            h.update(part)
    return h.hexdigest()


def _read_manifest(base: Path) -> dict:
    """Lê o manifest.json (opcional) e confere os hashes dos arquivos que ele cita."""
    mf = base / "manifest.json"
    if not mf.exists():
        return {}
    manifest = json.loads(mf.read_text(encoding="utf-8"))
    for nome, esperado in (manifest.get("hashes") or {}).items():
        alvo = base / Path(nome).name
        if not alvo.exists():
            continue
        if _sha256(alvo).lower() != str(esperado).lower().removeprefix("sha256:"):
            raise ValueError(f"{alvo.name}: hash não confere com o manifest.json; carga recusada")
    return manifest


class _Contador:
    def __init__(self) -> None:
        self.invalidas = 0


def _socios(path: Path, cont: _Contador) -> Iterator[dict]:
    from app.services.people import norm_name

    for r in _open_csv(path, SOCIOS_COLS):
        cnpj = _txt(r["cnpj_basico"], 8)
        nome = _txt(r["nome_socio"], 255)
        nome_norm = norm_name(r["nome_norm"] or r["nome_socio"])[:255]
        core = r["cpf_core"]
        if not cnpj or not nome or not nome_norm or not re.fullmatch(r"\d{6}", core):
            cont.invalidas += 1
            continue
        yield {
            "cnpj_basico": cnpj,
            "nome_norm": nome_norm,
            "cpf_core": core,
            "nome_socio": nome,
            "qualificacao_cod": _txt(r["qualificacao_cod"], 4),
            "data_entrada": _date(r["data_entrada"]),
            "faixa_etaria": _txt(r["faixa_etaria"], 4),
        }


def _empresas(path: Path, cont: _Contador) -> Iterator[dict]:
    for r in _open_csv(path, EMPRESAS_COLS):
        cnpj = _txt(r["cnpj_basico"], 8)
        if not cnpj:
            cont.invalidas += 1
            continue
        yield {
            "cnpj_basico": cnpj,
            "razao_social": _txt(r["razao_social"], 255),
            "nome_fantasia": _txt(r["nome_fantasia"], 255),
            "natureza_juridica_cod": _txt(r["natureza_juridica_cod"], 10),
            "capital_social": _float(r["capital_social"]),
            "porte_cod": _txt(r["porte_cod"], 4),
            "situacao_cadastral_cod": _txt(r["situacao_cadastral_cod"], 4),
            "data_situacao": _date(r["data_situacao"]),
            "data_inicio_atividade": _date(r["data_inicio_atividade"]),
            "cnae_principal": _txt(r["cnae_principal"], 10),
            "uf": _txt(r["uf"], 2),
            "municipio_cod": _txt(r["municipio_cod"], 10),
        }


def _dominio(path: Path, tipo: str) -> Iterator[dict]:
    for r in _open_csv(path, DOMINIO_COLS):
        cod, desc = _txt(r["cod"], 10), _txt(r["descricao"], 300)
        if cod and desc:
            yield {"tipo": tipo, "cod": cod, "descricao": desc}


async def _bulk(session: AsyncSession, table, rows: Iterator[dict]) -> int:
    """Insere em lotes; devolve quantas linhas entraram de fato (duplicatas são ignoradas)."""
    total, batch = 0, []
    for row in rows:
        batch.append(row)
        if len(batch) >= _CHUNK:
            total += (await session.execute(pg_insert(table).values(batch).on_conflict_do_nothing())).rowcount
            batch = []
    if batch:
        total += (await session.execute(pg_insert(table).values(batch).on_conflict_do_nothing())).rowcount
    return total


# ───────────────────────── carga ─────────────────────────


async def load_dir(session: AsyncSession, dir_path) -> Dict[str, Any]:
    """
    Substitui o conteúdo de sócios/empresas pelo do diretório, numa única transação: qualquer erro
    (cabeçalho, hash, diretório vazio, falha de banco) desfaz tudo e mantém os dados anteriores.
    """
    base = Path(dir_path)
    obrigatorios = {n: base / n for n in ("socios.csv", "empresas.csv", "qualificacoes.csv", "naturezas.csv")}
    ausentes = [n for n, p in obrigatorios.items() if not p.is_file()]
    if ausentes:
        raise ValueError(f"{base}: arquivos ausentes: {', '.join(ausentes)}")
    cnaes = base / "cnaes.csv"

    try:
        manifest = _read_manifest(base)
        mes = str(manifest.get("mes") or datetime.now(timezone.utc).strftime("%Y-%m"))[:7]
        cont_s, cont_e = _Contador(), _Contador()

        await session.execute(delete(RfbSocio))
        await session.execute(delete(RfbEmpresa))
        n_emp = await _bulk(session, RfbEmpresa.__table__, _empresas(obrigatorios["empresas.csv"], cont_e))
        n_soc = await _bulk(session, RfbSocio.__table__, _socios(obrigatorios["socios.csv"], cont_s))
        if n_soc == 0 or n_emp == 0:
            raise ValueError(f"RFB {mes}: {n_soc} sócios e {n_emp} empresas; carga descartada")

        tipos = ["qualificacao", "natureza", "porte", "situacao"] + (["cnae"] if cnaes.is_file() else [])
        await session.execute(delete(RfbDominio).where(RfbDominio.tipo.in_(tipos)))
        n_dom = await _bulk(session, RfbDominio.__table__, _dominio(obrigatorios["qualificacoes.csv"], "qualificacao"))
        n_dom += await _bulk(session, RfbDominio.__table__, _dominio(obrigatorios["naturezas.csv"], "natureza"))
        if cnaes.is_file():
            n_dom += await _bulk(session, RfbDominio.__table__, _dominio(cnaes, "cnae"))
        estaticos = [{"tipo": "porte", "cod": c, "descricao": d} for c, d in PORTES.items()]
        estaticos += [{"tipo": "situacao", "cod": c, "descricao": d} for c, d in SITUACOES.items()]
        n_dom += await _bulk(session, RfbDominio.__table__, iter(estaticos))

        resultado = {
            "mes": mes,
            "socios": n_soc,
            "empresas": n_emp,
            "dominios": n_dom,
            "socios_descartados": cont_s.invalidas,
            "empresas_descartadas": cont_e.invalidas,
        }
        stmt = pg_insert(RfbCarga).values(
            mes=mes, carregado_em=datetime.now(timezone.utc), contagens=resultado
        )
        await session.execute(
            stmt.on_conflict_do_update(
                index_elements=["mes"],
                set_={"carregado_em": stmt.excluded.carregado_em, "contagens": stmt.excluded.contagens},
            )
        )
        await session.commit()
    except BaseException:
        await session.rollback()
        raise
    logger.info(f"RFB {mes}: {n_soc} sócios, {n_emp} empresas, {cont_s.invalidas} sócios descartados.")
    return resultado


# ───────────────────────── consulta ─────────────────────────


def _iso(d: Optional[date]) -> Optional[str]:
    return d.isoformat() if d else None


async def lookup(session: AsyncSession, nome: str, cpf_core: Optional[str], limit: int = 20) -> List[dict]:
    """Participações do mesmo nome E mesmos 6 dígitos centrais do CPF (ativas primeiro, mais recentes antes)."""
    from app.services.people import norm_name

    if not cpf_core or not nome:
        return []
    qual, nat, cnae, porte, sit = (aliased(RfbDominio) for _ in range(5))
    e, s = RfbEmpresa, RfbSocio
    q = (
        select(
            s, e,
            qual.descricao.label("qualificacao"),
            nat.descricao.label("natureza"),
            cnae.descricao.label("cnae_desc"),
            porte.descricao.label("porte"),
            sit.descricao.label("situacao"),
        )
        .join(e, e.cnpj_basico == s.cnpj_basico)
        .outerjoin(qual, (qual.tipo == "qualificacao") & (qual.cod == s.qualificacao_cod))
        .outerjoin(nat, (nat.tipo == "natureza") & (nat.cod == e.natureza_juridica_cod))
        .outerjoin(cnae, (cnae.tipo == "cnae") & (cnae.cod == e.cnae_principal))
        .outerjoin(porte, (porte.tipo == "porte") & (porte.cod == e.porte_cod))
        .outerjoin(sit, (sit.tipo == "situacao") & (sit.cod == e.situacao_cadastral_cod))
        .where(s.cpf_core == cpf_core, s.nome_norm == norm_name(nome))
        .order_by(
            case((e.situacao_cadastral_cod == _SITUACAO_ATIVA, 0), else_=1),
            s.data_entrada.desc().nullslast(),
            s.cnpj_basico,
        )
        .limit(limit)
    )
    out = []
    for row in (await session.execute(q)).all():
        soc, emp = row[0], row[1]
        out.append(
            {
                "cnpj_basico": emp.cnpj_basico,
                "razao_social": emp.razao_social,
                "nome_fantasia": emp.nome_fantasia,
                "situacao_cadastral": row.situacao,
                "data_situacao": _iso(emp.data_situacao),
                "data_inicio_atividade": _iso(emp.data_inicio_atividade),
                "cnae_principal": emp.cnae_principal,
                "cnae_descricao": row.cnae_desc,
                "uf": emp.uf,
                "natureza_juridica": row.natureza,
                "porte": row.porte,
                "capital_social": emp.capital_social,
                "qualificacao": row.qualificacao,
                "data_entrada": _iso(soc.data_entrada),
                "faixa_etaria": soc.faixa_etaria,
                "confianca": "provavel",
                "motivo": "mesmo nome e mesmos 6 dígitos centrais do CPF",
            }
        )
    return out


# ───────────────────────── linha de comando ─────────────────────────


async def _main(dir_path: str) -> Dict[str, Any]:
    from app.core.cache import cache
    from app.persistence.session import async_engine, async_session_maker

    try:
        async with async_session_maker() as session:
            res = await load_dir(session, dir_path)
        await cache.clear_prefix("remuneration:person")
        return res
    finally:
        await async_engine.dispose()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("uso: python -m app.services.rfb /caminho/do/diretorio")
    print(json.dumps(asyncio.run(_main(sys.argv[1])), ensure_ascii=False))

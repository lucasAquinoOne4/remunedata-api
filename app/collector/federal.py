"""
app/collector/federal.py
Carga mensal de remuneração dos servidores civis do Executivo federal (SIAPE).

O ZIP do portal traz `*_Cadastro.csv` e `*_Remuneracao.csv`, unidos por Id_SERVIDOR_PORTAL
(chave comum REAL, diferente do Senado). Os arquivos NÃO vêm ordenados e o cadastro repete o id
(vários vínculos), então a junção usa um SQLite temporário em disco (memória constante) e fica com
o primeiro cadastro de cada id.

Idempotente: reexecutar um mês substitui os registros dele. Um mês já carregado é pulado SEM
nenhuma requisição ao portal (os dados mudam pouco: uma carga por mês basta).
"""

import csv
import io
import sqlite3
import tempfile
import unicodedata
import zipfile
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from app.collector.senado import parse_brl
from app.core.logging import get_logger
from app.infra.federal_client import FederalDownloadClient
from app.persistence.repositories import FederalRepository
from app.services.people import invalidate_period_stat

logger = get_logger(__name__)

CHUNK = 1000  # linhas por INSERT (cada linha tem ~28 colunas: < 32767 parâmetros)
_LOOKUP_BATCH = 500  # ids por consulta no SQLite (limite de 999 variáveis)

# campo interno -> nome (normalizado) da coluna no CSV de Cadastro
_CADASTRO_COLS = {
    "nome": "NOME",
    "cpf": "CPF",
    "matricula": "MATRICULA",
    "cargo": "DESCRICAO_CARGO",
    "funcao": "FUNCAO",
    "org_lotacao": "ORG_LOTACAO",
    "org_exercicio": "ORG_EXERCICIO",
    "orgao_superior": "ORGSUP_EXERCICIO",
    "tipo_vinculo": "TIPO_VINCULO",
    "situacao_vinculo": "SITUACAO_VINCULO",
    "regime_juridico": "REGIME_JURIDICO",
    "jornada": "JORNADA_DE_TRABALHO",
    "uf_exercicio": "UF_EXERCICIO",
}

# campo interno -> prefixo (normalizado) da coluna de valor no CSV de Remuneracao
_REM_COLS = {
    "remuneracao_basica_bruta": "REMUNERACAO BASICA BRUTA (R$)",
    "abate_teto": "ABATE-TETO (R$)",
    "gratificacao_natalina": "GRATIFICACAO NATALINA (R$)",
    "abate_teto_natalina": "ABATE-TETO DA GRATIFICACAO NATALINA (R$)",
    "ferias": "FERIAS (R$)",
    "outras_eventuais": "OUTRAS REMUNERACOES EVENTUAIS (R$)",
    "irrf": "IRRF (R$)",
    "pss_rpgs": "PSS/RPGS (R$)",
    "demais_deducoes": "DEMAIS DEDUCOES (R$)",
    "verbas_indenizatorias": "TOTAL DE VERBAS INDENIZATORIAS (R$)",
    "valor_liquido": "REMUNERACAO APOS DEDUCOES OBRIGATORIAS (R$)",
}


def _norm(text: str) -> str:
    """Cabeçalho normalizado: sem acento, maiúsculas, espaços colapsados."""
    plain = "".join(
        ch for ch in unicodedata.normalize("NFKD", text) if not unicodedata.combining(ch)
    )
    return " ".join(plain.upper().split())


def _open_csv(zf: zipfile.ZipFile, suffix: str):
    names = [n for n in zf.namelist() if n.endswith(suffix)]
    if not names:
        raise RuntimeError(f"ZIP federal sem arquivo *{suffix}")
    raw = zf.open(names[0])
    return csv.reader(io.TextIOWrapper(raw, encoding="latin-1", newline=""), delimiter=";")


def _index_header(header: List[str], wanted: Dict[str, str], *, prefix: bool) -> Dict[str, int]:
    """Mapeia campo interno -> posição da coluna; falha se o layout do portal mudou."""
    normalized = [_norm(h) for h in header]
    index: Dict[str, int] = {}
    for field, expected in wanted.items():
        for pos, name in enumerate(normalized):
            if name == expected or (prefix and name.startswith(expected)):
                index[field] = pos
                break
        else:
            raise RuntimeError(f"Coluna '{expected}' não encontrada no CSV federal (layout mudou?)")
    return index


def _cell(row: List[str], pos: Optional[int]) -> Optional[str]:
    if pos is None or pos >= len(row):
        return None
    value = row[pos].strip()
    return value or None


def build_cadastro_db(zf: zipfile.ZipFile, db_path: Path) -> int:
    """Carrega o Cadastro num SQLite em disco (primeiro cadastro de cada id vence)."""
    reader = _open_csv(zf, "_Cadastro.csv")
    header = next(reader)
    id_pos = [_norm(h) for h in header].index("ID_SERVIDOR_PORTAL")
    cols = _index_header(header, _CADASTRO_COLS, prefix=False)
    fields = list(_CADASTRO_COLS)

    conn = sqlite3.connect(db_path)
    conn.execute(f"CREATE TABLE cad (id TEXT PRIMARY KEY, {', '.join(fields)})")
    insert = f"INSERT OR IGNORE INTO cad VALUES ({', '.join('?' * (len(fields) + 1))})"
    batch, total = [], 0
    for row in reader:
        if len(row) <= id_pos:
            continue
        batch.append((row[id_pos].strip(), *[_cell(row, cols[f]) for f in fields]))
        if len(batch) >= 50_000:
            conn.executemany(insert, batch)
            total += len(batch)
            batch = []
    if batch:
        conn.executemany(insert, batch)
        total += len(batch)
    conn.commit()
    conn.close()
    return total


def iter_records(zip_path: Path, ano: int, mes: int, work_dir: Path) -> Iterator[Dict[str, Any]]:
    """Gera os registros do mês (Remuneracao + Cadastro) como dicts prontos para INSERT."""
    with zipfile.ZipFile(zip_path) as zf:
        db_path = work_dir / "cadastro.sqlite"
        total_cad = build_cadastro_db(zf, db_path)
        logger.info(f"Federal {mes:02d}/{ano}: cadastro indexado ({total_cad} linhas).")

        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        reader = _open_csv(zf, "_Remuneracao.csv")
        header = next(reader)
        id_pos = [_norm(h) for h in header].index("ID_SERVIDOR_PORTAL")
        nome_pos = [_norm(h) for h in header].index("NOME")
        cpf_pos = [_norm(h) for h in header].index("CPF")
        cols = _index_header(header, _REM_COLS, prefix=True)

        def flush(rows: List[List[str]]) -> Iterator[Dict[str, Any]]:
            ids = [r[id_pos].strip() for r in rows]
            cadastro: Dict[str, sqlite3.Row] = {}
            for i in range(0, len(ids), _LOOKUP_BATCH):
                part = ids[i : i + _LOOKUP_BATCH]
                marks = ",".join("?" * len(part))
                for found in conn.execute(f"SELECT * FROM cad WHERE id IN ({marks})", part):
                    cadastro[found["id"]] = found
            for row in rows:
                sid = row[id_pos].strip()
                if not sid.isdigit():
                    continue
                cad = cadastro.get(sid)
                valores = {f: parse_brl(_cell(row, cols[f])) for f in _REM_COLS}
                bruto = (
                    valores["remuneracao_basica_bruta"]
                    + valores["abate_teto"]
                    + valores["gratificacao_natalina"]
                    + valores["abate_teto_natalina"]
                    + valores["ferias"]
                    + valores["outras_eventuais"]
                )
                yield {
                    "id_servidor_portal": int(sid),
                    "ano": ano,
                    "mes": mes,
                    "nome": (_cell(row, nome_pos) or (cad["nome"] if cad else "") or "").strip(),
                    "cpf_mascarado": _cell(row, cpf_pos) or (cad["cpf"] if cad else None),
                    "matricula_mascarada": cad["matricula"] if cad else None,
                    "cargo": cad["cargo"] if cad else None,
                    "funcao": cad["funcao"] if cad else None,
                    "org_lotacao": cad["org_lotacao"] if cad else None,
                    "org_exercicio": cad["org_exercicio"] if cad else None,
                    "orgao_superior": cad["orgao_superior"] if cad else None,
                    "tipo_vinculo": cad["tipo_vinculo"] if cad else None,
                    "situacao_vinculo": cad["situacao_vinculo"] if cad else None,
                    "regime_juridico": cad["regime_juridico"] if cad else None,
                    "jornada": cad["jornada"] if cad else None,
                    "uf_exercicio": cad["uf_exercicio"] if cad else None,
                    **{k: v for k, v in valores.items() if k != "valor_liquido"},
                    "valor_bruto": round(bruto, 2),
                    "valor_liquido": valores["valor_liquido"],
                }

        pending: List[List[str]] = []
        seen = set()
        for row in reader:
            if len(row) <= id_pos or row[id_pos] in seen:
                continue
            seen.add(row[id_pos])
            pending.append(row)
            if len(pending) >= _LOOKUP_BATCH * 2:
                yield from flush(pending)
                pending = []
        if pending:
            yield from flush(pending)
        conn.close()


class FederalCollector:
    """Carrega um mês do Executivo federal a partir do ZIP do portal."""

    def __init__(self, client: FederalDownloadClient, repo: FederalRepository):
        self.client = client
        self.repo = repo

    async def collect_month(self, ano: int, mes: int, force: bool = False) -> Dict[str, Any]:
        # 1) Já carregado: nenhuma requisição ao portal
        if not force and await self.repo.month_loaded(ano, mes):
            return {"ano": ano, "mes": mes, "registros": 0, "status": "already_loaded"}

        # 2) Publicado? (um HEAD)
        if not await self.client.is_published(ano, mes):
            logger.info(f"Federal {mes:02d}/{ano}: ainda não publicado.")
            return {"ano": ano, "mes": mes, "registros": 0, "status": "empty"}

        # 3) Um download; junta Cadastro x Remuneracao em SQLite temporário e grava em lotes
        with tempfile.TemporaryDirectory(prefix="federal-") as tmp:
            work = Path(tmp)
            zip_path = work / "servidores.zip"
            await self.client.download(ano, mes, zip_path)
            total = await self.repo.replace_month(
                ano, mes, iter_records(zip_path, ano, mes, work), chunk=CHUNK
            )
        try:
            await invalidate_period_stat(self.repo.session, "FEDERAL", ano, mes)
        except Exception as exc:  # agregados e retrato são cache: nunca derrubam a carga
            logger.warning(f"Não foi possível atualizar agregados de {mes:02d}/{ano}: {exc}")
        logger.info(f"Federal {mes:02d}/{ano}: {total} registros gravados.")
        return {"ano": ano, "mes": mes, "registros": total, "status": "success"}

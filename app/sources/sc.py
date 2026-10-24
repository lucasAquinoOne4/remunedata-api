"""
app/sources/sc.py
Adaptador de Santa Catarina (portal CKAN https://dados.sc.gov.br, dataset `remuneracaoservidores`).

Fonte (confirmada em 22/10/2026):
  - package_show devolve todos os recursos numa única chamada (~100 KB). Os mensais se chamam
    `Servidores-Ativos-AAAA-MM.csv` / `Servidores-Inativos-AAAA-MM.csv` (também .json e .xlsx).
  - CSV: UTF-8 (sem BOM nos arquivos atuais, mas tolera-se BOM), separador `;`, decimal com vírgula,
    colunas Nome;CPF;Cargo;OrgaoExercicio;UnidadeExercicio;RegimeTrabalho;CargaHorariaCargo;
    CargaHorariaServidor;Situacao;ValorBruto. Sem paginação (~22 MB para os ativos de um mês).
  - O servidor ignora `Range`; baixa-se o arquivo inteiro em streaming para o disco.
  - NÃO há matrícula, rubricas, mês/ano nas colunas (só no nome do recurso) nem valor líquido:
    `valor_liquido` e `valor_descontos` ficam 0.0 (a fonte só publica o bruto); a interface os mostra
    como "não informado" (frontend: FONTES_SEM_LIQUIDO).
  - Licença Creative Commons Atribuição (cc-by): citar "Governo de Santa Catarina".

Regra de escolha do recurso (pode haver duplicatas, p.ex. versões reprocessadas): entre os CSV cujo
nome casa com `Servidores-(Ativos|Inativos)-AAAA-MM`, vence o de `last_modified` (ou `created`) mais
recente; em empate, o que aparece por último na lista do CKAN.

Identidade: não há matrícula; `ident` = md5[:16] de `NOME NORMALIZADO|6 dígitos centrais do CPF|ÓRGÃO`.
Limitação: duas pessoas com mesmo nome, mesmos dígitos do CPF e mesmo órgão colidem (muito raro).
"""

import csv
import hashlib
import io
import re
import time
import unicodedata
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

import httpx

from app.sources.base import ASYNC_HOOKS, SYNC_HOOKS

PACKAGE_URL = "https://dados.sc.gov.br/api/3/action/package_show"
PACKAGE_ID = "remuneracaoservidores"
_NAME_RE = re.compile(r"^servidores-(ativos|inativos)-(\d{4})-(\d{2})\.csv$", re.IGNORECASE)
_TIMEOUT = httpx.Timeout(connect=30.0, read=300.0, write=60.0, pool=30.0)
_HEADERS = {"User-Agent": "remunedata-api/1.0 (dados abertos SC)"}
_PAUSE = 1.0  # cortesia entre requisições ao portal


class SCSourceError(RuntimeError):
    """Falha de estrutura/rede ao ler a fonte de Santa Catarina."""


def _fold(text: Optional[str]) -> str:
    """Sem acento, maiúsculas, espaços colapsados."""
    if not text:
        return ""
    nfkd = unicodedata.normalize("NFKD", str(text))
    plain = "".join(c for c in nfkd if not unicodedata.combining(c))
    return " ".join(plain.upper().split())


def _col_key(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", _fold(name).lower())


def parse_decimal(raw: Optional[str]) -> Optional[float]:
    """Aceita '1234,56', '1234.56', '1.234,56', '1,234.56', 'R$ 1.000,00'. None se vazio/ilegível."""
    if raw is None:
        return None
    s = re.sub(r"[^\d,.\-]", "", str(raw))
    if not s or s in ("-", ",", "."):
        return None
    if "," in s and "." in s:
        dec = "," if s.rfind(",") > s.rfind(".") else "."
        thou = "." if dec == "," else ","
        s = s.replace(thou, "").replace(dec, ".")
    elif "," in s:
        s = s.replace(",", ".") if s.count(",") == 1 else s.replace(",", "")
    elif "." in s:
        parts = s.split(".")
        # vários pontos, ou um ponto seguido de exatamente 3 dígitos => separador de milhar
        if len(parts) > 2 or len(parts[-1]) == 3:
            s = "".join(parts)
    try:
        return float(s)
    except ValueError:
        return None


def _cpf_middle(cpf: Optional[str]) -> str:
    """6 dígitos centrais de um CPF mascarado ('***.237.349-**'); vazio se não houver."""
    digits = re.sub(r"\D", "", cpf or "")
    return digits[3:9] if len(digits) == 11 else digits


def make_ident(nome: str, cpf: Optional[str], orgao: Optional[str]) -> str:
    base = f"{_fold(nome)}|{_cpf_middle(cpf)}|{_fold(orgao)}"
    return hashlib.md5(base.encode("utf-8")).hexdigest()[:16]  # noqa: S324 - chave, não segurança


def pick_resources(package: Dict[str, Any], ano: int, mes: int) -> Dict[str, Dict[str, Any]]:
    """{'ativos': recurso, 'inativos': recurso} do mês (só as chaves que existem)."""
    best: Dict[str, tuple] = {}
    for pos, res in enumerate(package.get("result", {}).get("resources", [])):
        m = _NAME_RE.match((res.get("name") or "").strip())
        if not m or int(m.group(2)) != ano or int(m.group(3)) != mes or not res.get("url"):
            continue
        kind = m.group(1).lower()
        stamp = str(res.get("last_modified") or res.get("created") or "")
        key = (stamp, pos)
        if kind not in best or key >= best[kind][0]:
            best[kind] = (key, res)
    return {k: v[1] for k, v in best.items()}


def _decode_ok(path: Path) -> bool:
    """O arquivo inteiro é UTF-8 válido? (senão assume cp1252)."""
    dec = io.TextIOWrapper(io.FileIO(path), encoding="utf-8-sig", errors="strict")
    try:
        while dec.read(1 << 20):
            pass
        return True
    except UnicodeDecodeError:
        return False
    finally:
        dec.close()


def _read_csv(path: Path, situacao_padrao: str) -> Iterator[Dict[str, Any]]:
    encoding = "utf-8-sig" if _decode_ok(path) else "cp1252"
    with open(path, "r", encoding=encoding, errors="replace", newline="") as fh:
        header_line = fh.readline()
        if not header_line.strip():
            raise SCSourceError("CSV de SC vazio")
        delim = max((";", ",", "\t"), key=header_line.count)
        header = next(csv.reader([header_line], delimiter=delim))
        idx = {_col_key(h): i for i, h in enumerate(header)}
        for needed in ("nome", "valorbruto"):
            if needed not in idx:
                raise SCSourceError(f"CSV de SC sem a coluna essencial '{needed}': {header}")

        def cell(row: List[str], key: str) -> str:
            i = idx.get(key)
            return row[i].strip() if i is not None and i < len(row) else ""

        for row in csv.reader(fh, delimiter=delim):
            if not row or not any(c.strip() for c in row):
                continue
            nome = " ".join(cell(row, "nome").split())
            if not nome:
                continue
            cpf = cell(row, "cpf") or None
            orgao = cell(row, "orgaoexercicio") or None
            detalhes: Dict[str, float] = {}
            for chave, col in (
                ("carga_horaria_cargo", "cargahorariacargo"),
                ("carga_horaria_servidor", "cargahorariaservidor"),
            ):
                val = parse_decimal(cell(row, col))
                if val is not None:
                    detalhes[chave] = val
            yield {
                "ident": make_ident(nome, cpf, orgao),
                "nome": nome,
                "cpf_mascarado": cpf,
                "cargo": cell(row, "cargo") or None,
                "orgao": orgao,
                "vinculo": cell(row, "regimetrabalho") or None,
                "situacao": cell(row, "situacao").upper() or situacao_padrao,
                "valor_bruto": parse_decimal(cell(row, "valorbruto")) or 0.0,
                "valor_liquido": 0.0,  # a fonte não publica líquido
                "valor_descontos": 0.0,
                "detalhes": detalhes,
            }


class SCAdapter:
    uf = "SC"
    nome = "Governo de Santa Catarina"

    async def is_published(self, ano: int, mes: int) -> bool:
        try:
            async with httpx.AsyncClient(event_hooks=ASYNC_HOOKS, timeout=_TIMEOUT, headers=_HEADERS) as client:
                resp = await client.get(PACKAGE_URL, params={"id": PACKAGE_ID})
                resp.raise_for_status()
                return "ativos" in pick_resources(resp.json(), ano, mes)
        except (httpx.HTTPError, ValueError):
            return False

    def iter_rows(self, ano: int, mes: int, work_dir: Path) -> Iterator[Dict[str, Any]]:
        work_dir = Path(work_dir)
        work_dir.mkdir(parents=True, exist_ok=True)
        with httpx.Client(event_hooks=SYNC_HOOKS, timeout=_TIMEOUT, headers=_HEADERS, follow_redirects=True) as client:
            resp = client.get(PACKAGE_URL, params={"id": PACKAGE_ID})
            resp.raise_for_status()
            resources = pick_resources(resp.json(), ano, mes)
            if "ativos" not in resources:
                raise SCSourceError(f"SC {ano}-{mes:02d}: recurso de ativos não publicado")
            for kind in ("ativos", "inativos"):
                if kind not in resources:
                    continue
                time.sleep(_PAUSE)
                path = work_dir / f"sc-{kind}-{ano}-{mes:02d}.csv"
                try:
                    with client.stream("GET", resources[kind]["url"]) as dl:
                        dl.raise_for_status()
                        with open(path, "wb") as out:
                            for chunk in dl.iter_bytes(1 << 20):
                                out.write(chunk)
                    yield from _read_csv(path, "ATIVO" if kind == "ativos" else "INATIVO")
                finally:
                    path.unlink(missing_ok=True)


ADAPTER = SCAdapter()

"""
app/sources/pe.py
Adaptador do Governo de Pernambuco (portal CKAN https://dados.pe.gov.br).

Fonte: dataset `remuneracao-de-servidores`. Um único `package_show` lista ~930 recursos: para cada
mês há um CSV e um JSON de ATIVOS e de INATIVOS (20 a 35 MB cada CSV), sem datastore (não
paginável). Sem chave e sem captcha; publicado com cerca de 1 mês de defasagem.

Padrão do recurso (confirmado em 22/10/2026):
  nome  "Remuneração ativos 09/2026" / "Remuneração inativos 09/2026"
        (13º salário aparece como mês 13, ex.: "013/2025"; é ignorado aqui)
  url   .../download/2026_9_remuneracao_ativos.csv   (mês sem zero à esquerda; às vezes "-1")
O mês só existe no nome/URL do recurso, não nas colunas. Primeiro vale o nome, depois a URL.

Duplicatas: vários meses têm o mesmo CSV republicado (correções). Decisão: vence o recurso de
`created` mais recente (a versão mais nova do órgão), com desempate pelo maior tamanho.

CSV: separador ';', decimal com ponto, UTF-8 (sem BOM em 2026, mas BOM e latin-1 são tolerados),
fim de linha CRLF. Colunas (21): r_instituicao, r_cpf (mascarado), r_matricula, r_nome,
r_categoria, r_cargo, r_vencimento_cargo, r_funcao, r_gratificacao_funcao, r_remuneracao,
r_ferias, r_natalina, r_outras_vantagens, r_total_vantagens (= bruto), r_desconto_excedente,
r_descontos_faltas, r_descontos_previdencia, r_imposto_renda, r_descontos_compulsorios,
r_valor_liquido (= total_vantagens - descontos_compulsorios), r_outros_creditos.

Atenção: o servidor ignora o cabeçalho Range (devolve o arquivo inteiro com 200), então não há
como baixar só o começo; o download é feito em streaming para disco.
"""

import csv
import hashlib
import re
import time
import unicodedata
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

import httpx

from app.core.logging import get_logger
from app.sources.base import ASYNC_HOOKS, SYNC_HOOKS, NormalRow

logger = get_logger(__name__)

BASE_URL = "https://dados.pe.gov.br"
PACKAGE_ID = "remuneracao-de-servidores"
PACKAGE_URL = f"{BASE_URL}/api/3/action/package_show"
USER_AGENT = "remunedata-api/1.0 (+dados abertos; coleta mensal)"

_PAUSE = 1.0  # segundos entre requisições à fonte
_ATTEMPTS = 3

_NAME_RE = re.compile(r"\b(inativos|ativos)\b\s+0*(\d{1,2})\s*/\s*(\d{4})", re.I)
_URL_RE = re.compile(r"/(\d{4})_(\d{1,2})_remuneracao_(inativos|ativos)(?:-\d+)?\.csv$", re.I)

# colunas numéricas (nome normalizado -> chave de `detalhes`); só as diferentes de zero são gravadas
_DETALHES = (
    "vencimento_cargo",
    "remuneracao",
    "gratificacao_funcao",
    "ferias",
    "natalina",
    "outras_vantagens",
    "desconto_excedente",
    "descontos_faltas",
    "descontos_previdencia",
    "imposto_renda",
    "outros_creditos",
)
_ESSENCIAIS = ("nome", "total_vantagens", "valor_liquido")
_VAZIOS = {"", "NA", "N/A", "NULL", "NONE", "-"}


class PEFormatError(RuntimeError):
    """O CSV de Pernambuco mudou de formato (coluna essencial ausente)."""


# ------------------------------------------------------------------ utilidades


def _norm_header(name: str) -> str:
    text = unicodedata.normalize("NFKD", name.replace("﻿", ""))
    text = "".join(c for c in text if not unicodedata.combining(c)).strip().lower()
    text = re.sub(r"[^a-z0-9]+", "_", text).strip("_")
    return text[2:] if text.startswith("r_") else text


def parse_decimal(value: Optional[str]) -> float:
    """'1893.21' (padrão da fonte), '1.234,56' e '1234,5' viram float; vazio/lixo vira 0.0."""
    if value is None:
        return 0.0
    text = str(value).strip().replace("R$", "").replace(" ", "")
    if text.upper() in _VAZIOS:
        return 0.0
    if "," in text and "." in text:
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")  # 1.234,56
        else:
            text = text.replace(",", "")  # 1,234.56
    elif "," in text:
        text = text.replace(",", ".")
    try:
        return float(text)
    except ValueError:
        return 0.0


def _clean(value: Optional[str]) -> Optional[str]:
    text = (value or "").strip()
    return None if text.upper() in _VAZIOS else text


def resource_period(res: Dict[str, Any]) -> Optional[Tuple[str, int, int]]:
    """(tipo 'ativos'|'inativos', ano, mes) de um recurso CSV, ou None."""
    if str(res.get("format", "")).strip().lower().lstrip(".") != "csv":
        return None
    if str(res.get("state", "active")).lower() != "active":
        return None
    m = _NAME_RE.search(res.get("name") or "")
    if m:
        return m[1].lower(), int(m[3]), int(m[2])
    m = _URL_RE.search(res.get("url") or "")
    if m:
        return m[3].lower(), int(m[1]), int(m[2])
    return None


def pick_resource(resources: List[Dict[str, Any]], tipo: str, ano: int, mes: int) -> Optional[Dict]:
    """Recurso CSV do tipo/mês; entre duplicatas, o `created` mais recente (depois o maior)."""
    cands = [r for r in resources if resource_period(r) == (tipo, ano, mes) and r.get("url")]
    if not cands:
        return None
    return max(cands, key=lambda r: (r.get("created") or "", int(r.get("size") or 0)))


def make_ident(matricula: Optional[str], nome: str, orgao: Optional[str]) -> str:
    matricula = " ".join((matricula or "").split())
    if matricula:
        return matricula
    key = " ".join((nome or "").upper().split())
    return hashlib.md5(f"{key}|{' '.join((orgao or '').upper().split())}".encode()).hexdigest()[:16]


def _decoded_lines(path: Path) -> Iterator[str]:
    """Linhas em texto: UTF-8 (com ou sem BOM); linha que não decodifica cai para latin-1."""
    with open(path, "rb") as fh:
        for i, raw in enumerate(fh):
            if i == 0 and raw.startswith(b"\xef\xbb\xbf"):
                raw = raw[3:]
            try:
                yield raw.decode("utf-8")
            except UnicodeDecodeError:
                yield raw.decode("latin-1")


def parse_csv(path: Path, situacao: str) -> Iterator[NormalRow]:
    """Linhas normalizadas de um CSV de ativos/inativos."""
    reader = csv.reader(_decoded_lines(path), delimiter=";")
    header = next(reader, None)
    if header is None:
        return
    index = {}
    for pos, col in enumerate(header):
        index.setdefault(_norm_header(col), pos)
    missing = [c for c in _ESSENCIAIS if c not in index]
    if missing:
        raise PEFormatError(
            f"CSV de Pernambuco sem coluna essencial {missing}; cabeçalho: {sorted(index)}"
        )

    def get(cells: List[str], key: str) -> Optional[str]:
        pos = index.get(key)
        return cells[pos] if pos is not None and pos < len(cells) else None  # linha curta

    for cells in reader:
        if not cells or not any(c.strip() for c in cells):
            continue
        nome = (get(cells, "nome") or "").strip()
        if not nome:
            continue
        orgao = _clean(get(cells, "instituicao"))
        matricula = _clean(get(cells, "matricula"))
        bruto = parse_decimal(get(cells, "total_vantagens"))
        liquido = parse_decimal(get(cells, "valor_liquido"))
        descontos = parse_decimal(get(cells, "descontos_compulsorios"))
        if not descontos:
            descontos = max(bruto - liquido, 0.0)
        detalhes = {}
        for key in _DETALHES:
            val = parse_decimal(get(cells, key))
            if val:
                detalhes[key] = val
        yield {
            "ident": make_ident(matricula, nome, orgao),
            "matricula": matricula,
            "nome": nome,
            "cpf_mascarado": _clean(get(cells, "cpf")),
            "cargo": _clean(get(cells, "cargo")),
            "orgao": orgao,
            "vinculo": _clean(get(cells, "categoria")),
            "situacao": situacao,
            "valor_bruto": bruto,
            "valor_liquido": liquido,
            "valor_descontos": descontos,
            "detalhes": detalhes,
        }


# ------------------------------------------------------------------ adaptador


class PEAdapter:
    uf = "PE"
    nome = "Governo de Pernambuco"

    def _headers(self) -> Dict[str, str]:
        return {"User-Agent": USER_AGENT, "Accept": "*/*"}

    @staticmethod
    def _extract(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
        return (payload.get("result") or {}).get("resources") or []

    async def is_published(self, ano: int, mes: int) -> bool:
        async with httpx.AsyncClient(event_hooks=ASYNC_HOOKS, timeout=60, headers=self._headers()) as client:
            resp = await client.get(PACKAGE_URL, params={"id": PACKAGE_ID})
            resp.raise_for_status()
        return pick_resource(self._extract(resp.json()), "ativos", ano, mes) is not None

    def _package_resources(self, client: httpx.Client) -> List[Dict[str, Any]]:
        resp = client.get(PACKAGE_URL, params={"id": PACKAGE_ID})
        resp.raise_for_status()
        return self._extract(resp.json())

    def _download(self, client: httpx.Client, url: str, dest: Path) -> None:
        last: Optional[Exception] = None
        for attempt in range(1, _ATTEMPTS + 1):
            try:
                with client.stream("GET", url) as resp:
                    resp.raise_for_status()
                    with open(dest, "wb") as fh:
                        for chunk in resp.iter_bytes(1 << 20):
                            fh.write(chunk)
                return
            except (httpx.TransportError, httpx.HTTPStatusError) as exc:
                last = exc
                logger.warning(f"PE: falha ao baixar ({attempt}/{_ATTEMPTS}): {exc}")
                if attempt < _ATTEMPTS:
                    time.sleep(_PAUSE * attempt)
        raise RuntimeError(f"PE: download falhou após {_ATTEMPTS} tentativas: {last}")

    def iter_rows(self, ano: int, mes: int, work_dir: Path) -> Iterator[NormalRow]:
        work_dir.mkdir(parents=True, exist_ok=True)
        timeout = httpx.Timeout(60.0, read=600.0)
        with httpx.Client(event_hooks=SYNC_HOOKS, 
            timeout=timeout, headers=self._headers(), follow_redirects=True
        ) as client:
            resources = self._package_resources(client)
            plano = []
            for tipo, situacao in (("ativos", "ATIVO"), ("inativos", "INATIVO")):
                res = pick_resource(resources, tipo, ano, mes)
                if res is None:
                    if tipo == "ativos":
                        raise RuntimeError(f"PE {mes:02d}/{ano}: recurso de ativos não publicado")
                    logger.warning(
                        f"PE {mes:02d}/{ano}: sem recurso de inativos; seguindo só com ativos."
                    )
                    continue
                plano.append((tipo, situacao, res["url"]))
            for tipo, situacao, url in plano:
                time.sleep(_PAUSE)
                dest = work_dir / f"pe_{ano}_{mes:02d}_{tipo}.csv"
                try:
                    self._download(client, url, dest)
                    yield from parse_csv(dest, situacao)
                finally:
                    dest.unlink(missing_ok=True)


ADAPTER = PEAdapter()

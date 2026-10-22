"""
app/sources/go.py
Adaptador do Governo de Goiás (folha de pagamento da SEAD) no portal CKAN dadosabertos.go.gov.br.

Fonte: package_show?id=folha-de-pagamento. Existe um recurso CSV por mês ('Folha de Pagamento -
Agosto/2026', arquivo folhapagamento_202608.csv) com datastore ATIVO; anos até 2025 só têm ZIP anual
(sem datastore). LIMITAÇÃO: meses anteriores ao primeiro recurso mensal (hoje 2026) são tratados como
não publicados, para não baixar ZIPs de centenas de MB.

Leitura: datastore_search paginado (teto real de `limit` = 32000; ~167 mil linhas/mês => ~6 páginas).
Se o recurso do mês não tiver datastore, baixa o CSV em streaming para work_dir e lê linha a linha.
Nada do mês é mantido inteiro em memória.

Colunas: não há CPF nem matrícula. ident = md5[:16] de "NOME NORMALIZADO|CODORGAO".
DECISÃO DO BRUTO: valor_bruto = VALORPROVENTO (proventos totais). Verificado em amostra real:
VALORLIQUIDO == VALORPROVENTO - VALORDEMAISDESCONTOS. VALORPROVENTOMES (proventos só do mês, sem
férias/13º) é igual ao anterior nas linhas comuns; se VALORPROVENTO vier vazio usa-se PROVENTOMES.
"""

import asyncio
import csv
import hashlib
import re
import time
import unicodedata
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

import httpx

from app.sources.base import NormalRow

BASE_URL = "https://dadosabertos.go.gov.br"
PACKAGE_URL = f"{BASE_URL}/api/3/action/package_show"
SEARCH_URL = f"{BASE_URL}/api/3/action/datastore_search"
PACKAGE_ID = "folha-de-pagamento"

PAGE_SIZE = 32000  # teto da API (limit maior é cortado para 32000)
PAUSE = 0.5  # s entre páginas
MAX_ATTEMPTS = 5
BACKOFF = 2.0  # s; dobra a cada tentativa
TIMEOUT = httpx.Timeout(120.0, connect=20.0)

MESES = [
    "janeiro", "fevereiro", "março", "abril", "maio", "junho",
    "julho", "agosto", "setembro", "outubro", "novembro", "dezembro",
]  # fmt: skip

# coluna da fonte -> chave em detalhes (só gravada quando diferente de zero)
DETALHES = {
    "VALORFERIAS": "ferias",
    "VALORDECIMOTERCEIRO": "decimo_terceiro",
    "VALORDEMAISDESCONTOS": "demais_descontos",
    "VALORCORTETETO": "corte_teto",
}


class GoSourceError(RuntimeError):
    """Falha definitiva ao ler a fonte (nunca pulamos dados em silêncio)."""


def _num(value: Any) -> float:
    """Converte '1.234,56', '1234,56', '1234.56', '' e None com segurança."""
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace("R$", "").replace(" ", "")
    if not text:
        return 0.0
    if "," in text:
        text = text.replace(".", "").replace(",", ".")
    try:
        return float(text)
    except ValueError:
        return 0.0


def _strip_accents(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


def _norm_name(text: str) -> str:
    return re.sub(r"\s+", " ", _strip_accents(text or "")).strip().upper()


def make_ident(nome: str, codorgao: Any) -> str:
    key = f"{_norm_name(nome)}|{str(codorgao or '').strip()}"
    return hashlib.md5(key.encode("utf-8")).hexdigest()[:16]


def normalize(raw: Dict[str, Any]) -> Optional[NormalRow]:
    nome = (raw.get("NOMESERVIDOR") or "").strip()
    if not nome:
        return None
    bruto = _num(raw.get("VALORPROVENTO")) or _num(raw.get("VALORPROVENTOMES"))
    liquido = _num(raw.get("VALORLIQUIDO"))
    demais = _num(raw.get("VALORDEMAISDESCONTOS"))
    descontos = bruto - liquido if bruto > 0 and liquido > 0 else demais
    detalhes: Dict[str, float] = {}
    for col, key in DETALHES.items():
        v = _num(raw.get(col))
        if v:
            detalhes[key] = v
    mes_val = _num(raw.get("VALORPROVENTOMES"))
    if mes_val and mes_val != bruto:
        detalhes["provento_mes"] = mes_val
    return {
        "ident": make_ident(nome, raw.get("CODORGAO")),
        "nome": nome,
        "cargo": (raw.get("NOMECARGO") or "").strip() or None,
        "orgao": (raw.get("ORGAO") or "").strip() or None,
        "valor_bruto": bruto,
        "valor_liquido": liquido,
        "valor_descontos": round(descontos, 2),
        "detalhes": detalhes,
    }


def _pick_resource(resources: List[Dict[str, Any]], ano: int, mes: int) -> Optional[Dict[str, Any]]:
    """Recurso CSV mensal de ano/mês: pelo nome do arquivo (AAAAMM) ou pelo título 'Mês/AAAA'."""
    tag = f"{ano}{mes:02d}"
    title = f"{MESES[mes - 1]}/{ano}"
    for res in resources:
        if (res.get("format") or "").upper() != "CSV":
            continue
        url = (res.get("url") or "").lower()
        name = _strip_accents((res.get("name") or "").lower())
        if f"folhapagamento_{tag}" in url or _strip_accents(title) in name:
            return res
    return None


class GoAdapter:
    uf = "GO"
    nome = "Governo de Goiás"

    # ---------- HTTP ----------
    def _request(self, client: httpx.Client, url: str, params: Optional[dict] = None) -> httpx.Response:
        last = "sem resposta"
        for attempt in range(MAX_ATTEMPTS):
            try:
                resp = client.get(url, params=params)
            except httpx.TransportError as exc:
                last = f"{type(exc).__name__}"
            else:
                if resp.status_code == 429 or resp.status_code >= 500:
                    last = f"HTTP {resp.status_code}"
                    try:
                        wait = float(resp.headers.get("Retry-After", ""))
                    except ValueError:
                        wait = 0.0
                    if wait:
                        time.sleep(min(wait, 60))
                elif resp.status_code >= 400:
                    raise GoSourceError(f"GO: HTTP {resp.status_code} em {url}")
                else:
                    return resp
            if attempt < MAX_ATTEMPTS - 1:
                time.sleep(BACKOFF * (2**attempt))
        raise GoSourceError(f"GO: falha definitiva em {url} ({last})")

    def _json(self, client: httpx.Client, url: str, params: Optional[dict] = None) -> Dict[str, Any]:
        try:
            data = self._request(client, url, params).json()
        except ValueError as exc:
            raise GoSourceError(f"GO: resposta não é JSON em {url}") from exc
        if not data.get("success"):
            raise GoSourceError(f"GO: API recusou a consulta em {url}: {data.get('error')}")
        return data["result"]

    def _resource(self, client: httpx.Client, ano: int, mes: int) -> Optional[Dict[str, Any]]:
        pkg = self._json(client, PACKAGE_URL, {"id": PACKAGE_ID})
        return _pick_resource(pkg.get("resources", []), ano, mes)

    # ---------- datastore ----------
    def _datastore_rows(self, client: httpx.Client, resource_id: str) -> Iterator[Dict[str, Any]]:
        offset = 0
        while True:
            res = self._json(
                client,
                SEARCH_URL,
                {"resource_id": resource_id, "limit": PAGE_SIZE, "offset": offset, "sort": "_id"},
            )
            records = res.get("records") or []
            yield from records
            offset += len(records)
            total = res.get("total")
            if not records or (total is not None and offset >= int(total)):
                return
            time.sleep(PAUSE)

    # ---------- CSV (fallback) ----------
    def _csv_rows(self, client: httpx.Client, url: str, work_dir: Path) -> Iterator[Dict[str, Any]]:
        work_dir.mkdir(parents=True, exist_ok=True)
        path = work_dir / "go_folha.csv"
        last = "sem resposta"
        for attempt in range(MAX_ATTEMPTS):
            try:
                with client.stream("GET", url) as resp:
                    if resp.status_code == 429 or resp.status_code >= 500:
                        last = f"HTTP {resp.status_code}"
                    elif resp.status_code >= 400:
                        raise GoSourceError(f"GO: HTTP {resp.status_code} em {url}")
                    else:
                        with open(path, "wb") as fh:
                            for chunk in resp.iter_bytes(1 << 20):
                                fh.write(chunk)
                        break
            except httpx.TransportError as exc:
                last = type(exc).__name__
            if attempt == MAX_ATTEMPTS - 1:
                raise GoSourceError(f"GO: falha definitiva baixando CSV ({last})")
            time.sleep(BACKOFF * (2**attempt))
        try:
            with open(path, "r", encoding="utf-8-sig", newline="") as fh:
                head = fh.readline()
                fh.seek(0)
                delim = max(";,\t", key=head.count)
                yield from csv.DictReader(fh, delimiter=delim)
        finally:
            path.unlink(missing_ok=True)

    # ---------- contrato ----------
    def _is_published_sync(self, ano: int, mes: int) -> bool:
        with httpx.Client(timeout=TIMEOUT, follow_redirects=True) as client:
            res = self._resource(client, ano, mes)
            if not res:
                return False
            if res.get("datastore_active"):
                data = self._json(
                    client, SEARCH_URL, {"resource_id": res["id"], "limit": 1, "fields": "_id"}
                )
                return bool(data.get("records"))
            # sem datastore: confere se o CSV tem ao menos uma linha de dados (lê só o começo)
            with client.stream("GET", res["url"]) as resp:
                if resp.status_code >= 400:
                    return False
                lines = 0
                for _ in resp.iter_lines():
                    lines += 1
                    if lines >= 2:
                        return True
            return False

    async def is_published(self, ano: int, mes: int) -> bool:
        return await asyncio.to_thread(self._is_published_sync, ano, mes)

    def iter_rows(self, ano: int, mes: int, work_dir: Path) -> Iterator[NormalRow]:
        with httpx.Client(timeout=TIMEOUT, follow_redirects=True) as client:
            res = self._resource(client, ano, mes)
            if not res:
                raise GoSourceError(f"GO: sem recurso para {mes:02d}/{ano}")
            if res.get("datastore_active"):
                source = self._datastore_rows(client, res["id"])
            else:
                source = self._csv_rows(client, res["url"], Path(work_dir))
            for raw in source:
                row = normalize(raw)
                if row is not None:
                    yield row


ADAPTER = GoAdapter()

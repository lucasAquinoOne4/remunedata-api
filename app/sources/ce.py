"""
app/sources/ce.py
Adaptador do Governo do Ceará (Ceará Transparente): API REST JSON pública, sem chave nem captcha.

GET /transparencia/servidores/salarios?year=AAAA&month=M&page=N  (100 registros por página; um mês
tem ~190 mil registros, ~1.900 páginas). A resposta é {"sumary": {total_pages, current_page,
total_records, records_per_page}, "data": [registros]}. A fonte NÃO publica CPF nem matrícula (o `id`
é do registro mensal, instável), então `ident` é o md5 de "NOME NORMALIZADO|codigo_orgao".
"""

import hashlib
import re
import time
import unicodedata
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

import httpx

from app.core.logging import get_logger
from app.sources.base import ASYNC_HOOKS, SYNC_HOOKS, SourceIncomplete, NormalRow

logger = get_logger(__name__)

BASE_URL = "https://api-dados-abertos.cearatransparente.ce.gov.br/transparencia/servidores/salarios"
MIN_INTERVAL = 0.6  # s entre requisições
MAX_ATTEMPTS = 5
MAX_RETRY_AFTER = 120.0
TIMEOUT = httpx.Timeout(60.0, connect=15.0)

# componentes numéricos extras que vão para `detalhes` quando diferentes de zero
_DETALHES = ("diarias", "desconto_teto", "outros_descontos")


class CearaFetchError(RuntimeError):
    """Uma página não pôde ser obtida após todas as tentativas."""


def parse_money(value: Any) -> float:
    """Aceita número, '1234.56' e '1.234,56'; inválido vira 0.0."""
    if value is None or isinstance(value, bool):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value) if value == value and abs(value) != float("inf") else 0.0
    text = re.sub(r"[^\d,.\-]", "", str(value))
    if not text or text in ("-", ".", ","):
        return 0.0
    if "," in text:  # formato BR: ponto é milhar, vírgula é decimal
        text = text.replace(".", "").replace(",", ".")
    elif text.count(".") > 1:  # '1.234.567' -> milhar
        text = text.replace(".", "")
    try:
        number = float(text)
    except ValueError:
        return 0.0
    return number if number == number and abs(number) != float("inf") else 0.0


def normalize_name(name: str) -> str:
    stripped = unicodedata.normalize("NFKD", name or "")
    stripped = "".join(c for c in stripped if not unicodedata.combining(c))
    return " ".join(stripped.upper().split())


def make_ident(nome: str, codigo_orgao: Any) -> str:
    key = f"{normalize_name(nome)}|{str(codigo_orgao or '').strip()}"
    return hashlib.md5(key.encode("utf-8")).hexdigest()[:16]


def normalize_record(rec: Dict[str, Any]) -> Optional[NormalRow]:
    nome = " ".join(str(rec.get("nome_servidor") or "").split())
    if not nome:
        return None
    orgao = (rec.get("descricao_orgao") or "").strip() or (rec.get("sigla") or "").strip()
    detalhes = {}
    for key in _DETALHES:
        val = parse_money(rec.get(key))
        if val:
            detalhes[key] = val
    return {
        "ident": make_ident(nome, rec.get("codigo_orgao")),
        "nome": nome,
        "cargo": (rec.get("dsc_cargo") or "").strip() or None,
        "orgao": orgao or None,
        "situacao": (rec.get("situacao_funcional") or "").strip() or None,
        "valor_bruto": parse_money(rec.get("total_proventos")),
        "valor_liquido": parse_money(rec.get("proventos_liquidos")),
        "valor_descontos": parse_money(rec.get("total_descontos")),
        "detalhes": detalhes or None,
    }


def _records(payload: Any) -> List[Dict[str, Any]]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("data", "dados", "items", "results"):
            if isinstance(payload.get(key), list):
                return payload[key]
    return []


def _summary(payload: Any) -> Dict[str, Any]:
    if isinstance(payload, dict):
        for key in ("sumary", "summary"):
            if isinstance(payload.get(key), dict):
                return payload[key]
    return {}


def _to_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _retry_after(response: httpx.Response) -> Optional[float]:
    raw = response.headers.get("Retry-After")
    if not raw:
        return None
    try:
        return max(0.0, min(float(raw), MAX_RETRY_AFTER))
    except ValueError:
        return None


class CearaAdapter:
    uf = "CE"
    nome = "Governo do Ceará"

    def __init__(self) -> None:
        self._last_request = 0.0

    def _throttle(self) -> None:
        wait = MIN_INTERVAL - (time.monotonic() - self._last_request)
        if wait > 0:
            time.sleep(wait)
        self._last_request = time.monotonic()

    def _get_page(self, client: httpx.Client, ano: int, mes: int, page: int) -> Any:
        params = {"year": ano, "month": mes, "page": page}  # month sem zero à esquerda
        last_error = ""
        for attempt in range(1, MAX_ATTEMPTS + 1):
            self._throttle()
            delay: Optional[float] = None
            try:
                resp = client.get(BASE_URL, params=params)
            except httpx.HTTPError as exc:
                last_error = type(exc).__name__
            else:
                if resp.status_code == 200:
                    try:
                        return resp.json()
                    except ValueError:
                        last_error = "JSON inválido"
                elif resp.status_code == 429 or resp.status_code >= 500:
                    last_error = f"HTTP {resp.status_code}"
                    delay = _retry_after(resp)
                else:
                    raise CearaFetchError(
                        f"CE {mes:02d}/{ano} página {page}: HTTP {resp.status_code}"
                    )
            if attempt < MAX_ATTEMPTS:
                pause = delay if delay is not None else min(2.0**attempt, 30.0)
                logger.warning(f"CE página {page}: {last_error}; nova tentativa em {pause:.1f}s.")
                time.sleep(pause)
        raise CearaFetchError(
            f"CE {mes:02d}/{ano} página {page}: falhou após {MAX_ATTEMPTS} tentativas ({last_error})"
        )

    async def is_published(self, ano: int, mes: int) -> bool:
        with httpx.Client(event_hooks=SYNC_HOOKS, timeout=TIMEOUT) as client:
            try:
                resp = client.get(BASE_URL, params={"year": ano, "month": mes, "page": 1})
                if resp.status_code != 200:
                    return False
                payload = resp.json()
            except (httpx.HTTPError, ValueError):
                return False
        return _to_int(_summary(payload).get("total_records")) > 0 and bool(_records(payload))

    def iter_rows(self, ano: int, mes: int, work_dir: Path) -> Iterator[NormalRow]:
        with httpx.Client(event_hooks=SYNC_HOOKS, timeout=TIMEOUT) as client:
            page, total_pages, declared, seen = 1, None, 0, 0
            while total_pages is None or page <= total_pages:
                payload = self._get_page(client, ano, mes, page)
                if total_pages is None:
                    total_pages = _to_int(_summary(payload).get("total_pages"))
                    declared = _to_int(_summary(payload).get("total_records"))
                records = _records(payload)
                if not records:
                    if total_pages and page <= total_pages:
                        raise CearaFetchError(f"CE {mes:02d}/{ano}: página {page} vazia")
                    break
                seen += len(records)
                for rec in records:
                    row = normalize_record(rec)
                    if row is not None:
                        yield row
                if page % 100 == 0:
                    logger.info(f"CE {mes:02d}/{ano}: página {page}/{total_pages}")
                page += 1
            # a própria API declara o total; menos que ~99% dele é carga truncada
            if declared and seen < declared * 0.99:
                raise SourceIncomplete(f"CE {mes:02d}/{ano}: {seen} registros de {declared} declarados")


ADAPTER = CearaAdapter()

"""
app/sources/ju.py
Adaptador "Justiça" (código JU): remuneração de membros do sistema de Justiça do DF (TJDFT, MPDFT,
TRE-DF por padrão) via DadosJusBR (https://api.dadosjusbr.org/v2), projeto aberto que padroniza os
contracheques publicados pelos próprios órgãos.

Fonte (confirmada em 23/10/2026, GET público sem chave):
  - /v2/dados/{orgao}/{ano}/{mes} traz metadados e `pacote_de_dados.url` (ZIP no S3 público).
  - O ZIP tem `contracheque.csv` (;, decimal com vírgula): nome, matricula (pode vir vazia), funcao,
    local_trabalho, salario (base), beneficios (outras verbas), descontos, remuneracao (= líquido).
  - Sem CPF: o cruzamento com outras fontes é só por nome (confiança "possível").

Mapeamento: valor_bruto = salario + beneficios; valor_liquido = remuneracao; órgão = sigla do órgão.
Meses que o DadosJusBR marca como `inconsistente` são carregados com `detalhes.fonte_inconsistente`.
As verbas indenizatórias e retroativas (ex.: R$ 150 mil em "benefícios") são pagas legalmente, mas
inflam o mês; o perfil deve apresentá-las como estão na fonte, sem juízo.
"""

import csv
import hashlib
import io
import re
import unicodedata
import zipfile
from pathlib import Path
from typing import Any, Dict, Iterator, List
from urllib.parse import urlparse

import httpx

from app.sources.base import ASYNC_HOOKS, SYNC_HOOKS

from app.core.config import get_settings

API = "https://api.dadosjusbr.org/v2"
_ALLOWED_HOSTS = {"api.dadosjusbr.org", "dadosjusbr-public.s3.amazonaws.com"}
_MAX_ZIP_BYTES = 150 * 1024 * 1024
_TIMEOUT = httpx.Timeout(connect=30.0, read=120.0, write=30.0, pool=30.0)
_HEADERS = {"User-Agent": "remunedata-api/1.0 (dados abertos DadosJusBR)"}


class JUSourceError(RuntimeError):
    """Falha de estrutura/rede ao ler o DadosJusBR."""


def _orgaos() -> List[str]:
    raw = get_settings().dadosjusbr_orgaos
    return [o.strip().lower() for o in raw.split(",") if re.fullmatch(r"[a-z0-9-]{2,30}", o.strip().lower())]


def _fold(text: str) -> str:
    plain = "".join(c for c in unicodedata.normalize("NFKD", text or "") if not unicodedata.combining(c))
    return " ".join(plain.upper().split())


def _num(raw: str) -> float:
    try:
        return float((raw or "0").strip().replace(".", "").replace(",", "."))
    except ValueError:
        return 0.0


def _check_host(url: str) -> None:
    if urlparse(url).hostname not in _ALLOWED_HOSTS:
        raise JUSourceError(f"host não permitido: {url}")


def parse_contracheque(text: str, orgao: str, inconsistente: bool = False) -> Iterator[Dict[str, Any]]:
    for r in csv.DictReader(io.StringIO(text), delimiter=";"):
        nome = (r.get("nome") or "").strip()
        if not nome:
            continue
        matricula = (r.get("matricula") or "").strip() or None
        salario, beneficios = _num(r.get("salario")), _num(r.get("beneficios"))
        detalhes: Dict[str, Any] = {"salario_base": salario, "beneficios": beneficios}
        if (r.get("local_trabalho") or "").strip():
            detalhes["lotacao"] = r["local_trabalho"].strip()
        if inconsistente:
            detalhes["fonte_inconsistente"] = True
        yield {
            "ident": hashlib.md5(f"{_fold(nome)}|{orgao}|{matricula or ''}".encode()).hexdigest()[:16],
            "nome": nome,
            "matricula": matricula,
            "cargo": (r.get("funcao") or "").strip() or None,
            "orgao": orgao.upper(),
            "situacao": (r.get("situacao") or "").strip() or "ATIVO",
            "valor_bruto": salario + beneficios,
            "valor_liquido": _num(r.get("remuneracao")),
            "valor_descontos": _num(r.get("descontos")),
            "detalhes": detalhes,
        }


class JUAdapter:
    uf = "JU"
    nome = "Justiça do DF (DadosJusBR)"

    def _month(self, client: httpx.Client, orgao: str, ano: int, mes: int) -> Dict[str, Any] | None:
        r = client.get(f"{API}/dados/{orgao}/{ano}/{mes}")
        if r.status_code in (204, 404):  # 204: o órgão ainda não publicou o mês
            return None
        r.raise_for_status()
        data = r.json()
        return data if (data.get("pacote_de_dados") or {}).get("url") else None

    async def is_published(self, ano: int, mes: int) -> bool:
        try:
            async with httpx.AsyncClient(event_hooks=ASYNC_HOOKS, timeout=_TIMEOUT, headers=_HEADERS) as client:
                for orgao in _orgaos():
                    r = await client.get(f"{API}/dados/{orgao}/{ano}/{mes}")
                    if r.status_code == 200 and (r.json().get("pacote_de_dados") or {}).get("url"):
                        return True  # 204/404: este órgão ainda não publicou; tenta o próximo
        except (httpx.HTTPError, ValueError):
            return False
        return False

    def iter_rows(self, ano: int, mes: int, work_dir: Path) -> Iterator[Dict[str, Any]]:
        work_dir = Path(work_dir)
        work_dir.mkdir(parents=True, exist_ok=True)
        found = 0
        with httpx.Client(event_hooks=SYNC_HOOKS, timeout=_TIMEOUT, headers=_HEADERS) as client:
            for orgao in _orgaos():
                meta = self._month(client, orgao, ano, mes)
                if meta is None:
                    continue  # órgão ainda não publicou este mês
                url = meta["pacote_de_dados"]["url"]
                _check_host(url)
                path = work_dir / f"{orgao}-{ano}-{mes:02d}.zip"
                size = 0
                with client.stream("GET", url) as dl:
                    dl.raise_for_status()
                    with open(path, "wb") as out:
                        for chunk in dl.iter_bytes(1 << 20):
                            size += len(chunk)
                            if size > _MAX_ZIP_BYTES:
                                raise JUSourceError(f"{url}: maior que o limite")
                            out.write(chunk)
                try:
                    with zipfile.ZipFile(path) as zf:
                        raw = zf.read("contracheque.csv")
                finally:
                    path.unlink(missing_ok=True)
                try:
                    text = raw.decode("utf-8-sig")
                except UnicodeDecodeError:
                    text = raw.decode("cp1252", errors="replace")
                for row in parse_contracheque(text, orgao, bool(meta.get("inconsistente"))):
                    found += 1
                    yield row
        if not found:
            raise JUSourceError(f"JU {ano}-{mes:02d}: nenhum órgão publicou linhas")


ADAPTER = JUAdapter()

"""
app/collector/senado.py
Coletor mensal de remuneração do Senado Federal.

Une a folha do mês ao cadastro de servidores pelo NOME (normalizado), e só quando o nome é
único no cadastro. O `sequencial` NÃO é uma chave comum entre os dois endpoints (verificado:
em 1.611 casos o número coincide, mas pertence a pessoas diferentes). Homônimos ficam sem
cadastro, em vez de arriscar atribuir cargo/lotação à pessoa errada.

A gravação é idempotente: reexecutar um mês substitui os registros daquele mês.
"""

import json
import unicodedata
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional

from app.core.logging import get_logger
from app.infra.senado_client import SenadoClient
from app.persistence.models import SenadoRemuneracao
from app.persistence.repositories import SenadoRepository

logger = get_logger(__name__)

# Campos que compõem o valor bruto (proventos). Previdência, IR, faltas e reversão do teto
# constitucional são deduções e ficam de fora.
PROVENTOS = (
    "remuneracao_basica",
    "vantagens_pessoais",
    "funcao_comissionada",
    "gratificacao_natalina",
    "horas_extras",
    "outras_eventuais",
    "abono_permanencia",
    "vantagens_indenizatorias",
    "diarias",
    "auxilios",
)


def parse_brl(value: Any) -> float:
    """Converte '5.612,35' (ou número) em float. Valores inválidos viram 0.0."""
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(".", "").replace(",", ".")
    try:
        return float(Decimal(text))
    except (InvalidOperation, ValueError):
        return 0.0


def normalize_name(name: Any) -> str:
    """Chave de junção: maiúsculas, sem acento e com espaços colapsados."""
    text = unicodedata.normalize("NFKD", str(name or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return " ".join(text.upper().split())


def index_cadastro(cadastro_list: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Indexa o cadastro por nome normalizado, descartando nomes repetidos (homônimos)."""
    counts: Dict[str, int] = {}
    by_name: Dict[str, Dict[str, Any]] = {}
    for entry in cadastro_list:
        key = normalize_name(entry.get("nome"))
        if not key:
            continue
        counts[key] = counts.get(key, 0) + 1
        by_name[key] = entry
    return {k: v for k, v in by_name.items() if counts[k] == 1}


def _nome(value: Any) -> Optional[str]:
    """Cargo/função vêm como texto ou como objeto {'nome': ...}."""
    if isinstance(value, dict):
        value = value.get("nome")
    return str(value).strip() if value else None


def build_record(item: Dict[str, Any], cadastro: Optional[Dict[str, Any]]) -> SenadoRemuneracao:
    """Monta o modelo a partir de um item da folha e do cadastro (se houver)."""
    valores = {
        c: parse_brl(item.get(c))
        for c in (
            *PROVENTOS,
            "previdencia",
            "imposto_renda",
            "faltas",
            "reversao_teto_constitucional",
        )
    }
    lotacao = (cadastro or {}).get("lotacao") or {}
    return SenadoRemuneracao(
        sequencial=int(item["sequencial"]),
        nome=(item.get("nome") or (cadastro or {}).get("nome") or "").strip(),
        ano=int(item["ano"]),
        mes=int(item["mes"]),
        tipo_folha=item.get("tipo_folha") or "Normal",
        vinculo=(cadastro or {}).get("vinculo"),
        situacao=(cadastro or {}).get("situacao"),
        cargo=_nome((cadastro or {}).get("cargo")),
        funcao=_nome((cadastro or {}).get("funcao")),
        lotacao_sigla=lotacao.get("sigla"),
        lotacao_nome=lotacao.get("nome"),
        valor_bruto=round(sum(valores[c] for c in PROVENTOS), 2),
        valor_liquido=parse_brl(item.get("remuneracao_liquida")),
        raw_payload_json=json.dumps(item, ensure_ascii=False),
        created_at=datetime.now(timezone.utc),
        **valores,
    )


class SenadoCollector:
    """Coleta e persiste um mês de remuneração do Senado."""

    def __init__(self, client: SenadoClient, repo: SenadoRepository):
        self.client = client
        self.repo = repo

    async def collect_month(self, ano: int, mes: int) -> Dict[str, Any]:
        folha = await self.client.get_remuneracoes(ano, mes)
        if not folha:
            logger.info(f"Senado {mes:02d}/{ano}: sem dados publicados.")
            return {"ano": ano, "mes": mes, "registros": 0, "status": "empty"}

        cadastro_list = await self.client.get_servidores()
        cadastro = index_cadastro(cadastro_list)

        records: List[SenadoRemuneracao] = []
        seen = set()
        for item in folha:
            try:
                key = (int(item["sequencial"]), item.get("tipo_folha") or "Normal")
            except (KeyError, TypeError, ValueError):
                continue
            if key in seen:  # duplicata dentro do mês violaria a unique constraint
                continue
            seen.add(key)
            records.append(build_record(item, cadastro.get(normalize_name(item.get("nome")))))

        await self.repo.replace_month(ano, mes, records)
        logger.info(f"Senado {mes:02d}/{ano}: {len(records)} registros gravados.")
        return {"ano": ano, "mes": mes, "registros": len(records), "status": "success"}

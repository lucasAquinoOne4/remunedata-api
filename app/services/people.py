"""
app/services/people.py
Consultas transversais às três fontes (DF, Senado, Executivo federal): períodos disponíveis,
estatísticas para gráficos, sugestões, perfil enriquecido da pessoa, comparação com pares e
cruzamento entre fontes.

Todas as consultas usam colunas indexadas (ver migrations f7a1, g8b2 e h9c3) e o resultado é
cacheado no Redis pelos endpoints: estas funções só calculam.
"""

import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import httpx
from sqlalchemy import Integer, String, cast, delete, func, insert, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.persistence.models import (
    ExecutionAnnual,
    ExecutionMonthly,
    FederalRemuneracao,
    PeriodStat,
    PersonLatest,
    RemunerationCollected,
    SenadoRemuneracao,
)

SOURCES = ("DF", "SENADO", "FEDERAL")


@dataclass(frozen=True)
class Spec:
    """Mapeamento das colunas comuns de cada fonte."""

    fonte: str
    model: Any
    ano: Any
    mes: Any  # expressão inteira
    nome: Any
    cargo: Any
    orgao: Any
    bruto: Any
    liquido: Any
    ident: Any


SPECS: Dict[str, Spec] = {
    "DF": Spec(
        "DF",
        RemunerationCollected,
        RemunerationCollected.ano_exercicio,
        cast(RemunerationCollected.mes_referencia, Integer),
        RemunerationCollected.nome_servidor,
        RemunerationCollected.cargo,
        RemunerationCollected.nome_orgao,
        RemunerationCollected.valor_bruto,
        RemunerationCollected.valor_liquido,
        RemunerationCollected.codigo_identificacao,
    ),
    "SENADO": Spec(
        "SENADO",
        SenadoRemuneracao,
        SenadoRemuneracao.ano,
        SenadoRemuneracao.mes,
        SenadoRemuneracao.nome,
        SenadoRemuneracao.cargo,
        SenadoRemuneracao.lotacao_nome,
        SenadoRemuneracao.valor_bruto,
        SenadoRemuneracao.valor_liquido,
        SenadoRemuneracao.sequencial,
    ),
    "FEDERAL": Spec(
        "FEDERAL",
        FederalRemuneracao,
        FederalRemuneracao.ano,
        FederalRemuneracao.mes,
        FederalRemuneracao.nome,
        FederalRemuneracao.cargo,
        FederalRemuneracao.org_exercicio,
        FederalRemuneracao.valor_bruto,
        FederalRemuneracao.valor_liquido,
        FederalRemuneracao.id_servidor_portal,
    ),
}

# Faixas (R$) do histograma de remuneração bruta
_FAIXAS = [
    (0, 3_000, "até 3 mil"),
    (3_000, 6_000, "3 a 6 mil"),
    (6_000, 10_000, "6 a 10 mil"),
    (10_000, 15_000, "10 a 15 mil"),
    (15_000, 25_000, "15 a 25 mil"),
    (25_000, 40_000, "25 a 40 mil"),
    (40_000, None, "acima de 40 mil"),
]


def strip_accents(text: str) -> str:
    return "".join(
        ch for ch in unicodedata.normalize("NFKD", text) if not unicodedata.combining(ch)
    )


def norm_name(text: Optional[str]) -> str:
    return " ".join(strip_accents(text or "").upper().split())


def like_exact(text: str) -> str:
    """Padrão ILIKE de igualdade (sem curinga), com escape, para usar o índice trigram."""
    plain = strip_accents(text)
    for ch in ("\\", "%", "_"):
        plain = plain.replace(ch, "\\" + ch)
    return plain


def cpf_core(cpf: Optional[str]) -> Optional[str]:
    """6 dígitos centrais do CPF mascarado ('***927115**' ou '***.411.707-**')."""
    digits = re.sub(r"\D", "", cpf or "")
    return digits if len(digits) == 6 else None


# ───────────────────────── períodos ─────────────────────────


async def periods(session: AsyncSession, fonte: str, limit: int = 36) -> List[dict]:
    """
    Competências carregadas (mais recente primeiro). No DF vem do registro de execuções (tabela
    pequena); varrer 4,7 milhões de linhas só para listar os meses levava ~10 s.
    """
    if fonte == "DF":
        rows = await session.execute(
            select(ExecutionAnnual.ano_exercicio, ExecutionMonthly.mes_referencia)
            .join(ExecutionAnnual, ExecutionAnnual.id == ExecutionMonthly.execution_id)
            .where(ExecutionMonthly.registros_coletados > 0)
            .distinct()
            .order_by(ExecutionAnnual.ano_exercicio.desc(), ExecutionMonthly.mes_referencia.desc())
            .limit(limit)
        )
        return [{"ano": int(a), "mes": f"{int(m):02d}"} for a, m in rows.all()]
    s = SPECS[fonte]
    rows = await session.execute(
        select(s.ano, s.mes).distinct().order_by(s.ano.desc(), s.mes.desc()).limit(limit)
    )
    return [{"ano": int(a), "mes": f"{int(m):02d}"} for a, m in rows.all()]


# ───────────────────────── estatísticas (gráficos) ─────────────────────────


def _age_seconds(moment) -> float:
    from datetime import datetime, timezone

    return (datetime.now(timezone.utc) - moment).total_seconds() if moment else 1e9


async def _monthly_series(session: AsyncSession, fonte: str, s: Spec, ano: Optional[int]) -> list:
    """
    Série mensal (registros, média e total bruto) lida da tabela period_stat. Meses ausentes são
    calculados UM a UM (cada um usa o índice por competência) e gravados; os 2 mais recentes são
    sempre recalculados porque ainda podem estar em carga.
    """
    plist = [p for p in await periods(session, fonte, 24) if (not ano or p["ano"] == ano)]
    stored = {
        (r.ano, r.mes): r
        for r in (
            await session.execute(select(PeriodStat).where(PeriodStat.fonte == fonte))
        ).scalars()
    }
    out = []
    for i, p in enumerate(plist):
        a_, m_ = p["ano"], int(p["mes"])
        row = stored.get((a_, m_))
        stale = row is not None and i < 2 and _age_seconds(row.updated_at) > 900
        if row is None or stale:
            cnt, avg, tot = (
                await session.execute(
                    select(func.count(), func.avg(s.bruto), func.sum(s.bruto)).where(
                        s.ano == a_, s.mes == m_
                    )
                )
            ).one()
            values = {
                "registros": int(cnt or 0),
                "media_bruto": float(avg or 0),
                "total_bruto": float(tot or 0),
            }
            stmt = pg_insert(PeriodStat).values(fonte=fonte, ano=a_, mes=m_, **values)
            await session.execute(
                stmt.on_conflict_do_update(
                    index_elements=["fonte", "ano", "mes"],
                    set_={**values, "updated_at": func.now()},
                )
            )
        else:
            values = {
                "registros": row.registros,
                "media_bruto": row.media_bruto,
                "total_bruto": row.total_bruto,
            }
        out.append({"ano": a_, "mes": p["mes"], **values})
    await session.commit()
    return list(reversed(out))  # cronológico crescente


async def invalidate_period_stat(session: AsyncSession, fonte: str, ano: int, mes: int) -> None:
    """
    Chamado quando a carga de um mês termina: descarta o agregado da competência e, se ela for a
    mais recente da fonte, reconstrói o retrato por pessoa.
    """
    await session.execute(
        delete(PeriodStat).where(
            PeriodStat.fonte == fonte, PeriodStat.ano == ano, PeriodStat.mes == int(mes)
        )
    )
    await session.commit()
    plist = await periods(session, fonte, 1)
    if plist and plist[0]["ano"] == ano and int(plist[0]["mes"]) == int(mes):
        await refresh_person_latest(session, fonte)


async def stats(session: AsyncSession, fonte: str, ano: Optional[int]) -> dict:
    """Agregados para o dashboard. O corte 'atual' é o mês mais recente do ano pedido."""
    s = SPECS[fonte]
    plist = [p for p in await periods(session, fonte, 24) if (not ano or p["ano"] == ano)]
    latest = (plist[0]["ano"], int(plist[0]["mes"])) if plist else None
    if latest is None:
        return {
            "fonte": fonte,
            "total_servidores": 0,
            "media_bruto": 0.0,
            "total_bruto": 0.0,
            "mensal": [],
            "top_orgaos": [],
            "faixas": [],
        }
    l_ano, l_mes = int(latest[0]), int(latest[1])

    mensal = await _monthly_series(session, fonte, s, ano)

    snap = (await snapshots(session)).get(fonte)
    if snap and (snap["ano"], snap["mes"]) == (l_ano, l_mes):
        # O retrato por pessoa é exatamente "o período mais recente, uma linha por pessoa":
        # agrega nele (tabela pequena, em memória) em vez de varrer a tabela mensal inteira.
        t, ano_col, mes_col, bruto, orgao = (
            PersonLatest, PersonLatest.ano, PersonLatest.mes, PersonLatest.valor_bruto, PersonLatest.orgao,
        )  # fmt: skip
        atual = (t.fonte == fonte, ano_col == l_ano, mes_col == l_mes)
        orgao_col = orgao
    else:
        t, bruto, orgao_col = s.model, s.bruto, s.orgao
        atual = (s.ano == l_ano, s.mes == l_mes)
    buckets = [
        func.count().filter((bruto >= lo) & ((bruto < hi) if hi is not None else True))
        for lo, hi, _ in _FAIXAS
    ]
    agg = (
        await session.execute(
            select(func.count(), func.avg(bruto), func.sum(bruto), *buckets).where(*atual)
        )
    ).one()
    totals, counts = agg[:3], agg[3:]

    top = (
        await session.execute(
            select(orgao_col, func.count().label("n"), func.avg(bruto))
            .where(*atual, orgao_col.isnot(None))
            .group_by(orgao_col)
            .order_by(func.count().desc())
            .limit(10)
        )
    ).all()

    return {
        "fonte": fonte,
        "total_servidores": int(totals[0] or 0),
        "media_bruto": float(totals[1] or 0),
        "total_bruto": float(totals[2] or 0),
        "mensal": mensal,
        "top_orgaos": [
            {"nome": (n or "").strip(), "registros": int(c), "media_bruto": float(avg or 0)}
            for n, c, avg in top
        ],
        "faixas": [
            {"faixa": label, "registros": int(counts[i] or 0)}
            for i, (_, _, label) in enumerate(_FAIXAS)
        ],
    }


# ───────────────────────── sugestões ─────────────────────────


async def suggest(
    session: AsyncSession, fonte: str, q: str, latest: Optional[dict] = None
) -> List[dict]:
    """Nomes que contêm `q` (pesquisa dinâmica), lidos do retrato por pessoa (tabela pequena)."""
    pattern = f"%{like_exact(q)}%"
    rows = (
        await session.execute(
            select(PersonLatest.nome, PersonLatest.cargo, PersonLatest.orgao)
            .where(
                PersonLatest.fonte == fonte,
                func.immutable_unaccent(PersonLatest.nome).ilike(pattern, escape="\\"),
            )
            .limit(60)
        )
    ).all()
    seen, out = set(), []
    for nome, cargo, orgao in rows:
        key = norm_name(nome)
        if key in seen:
            continue
        seen.add(key)
        out.append(
            {
                "nome": (nome or "").strip(),
                "fonte": fonte,
                "cargo": (cargo or None),
                "orgao": ((orgao or "").strip() or None),
            }
        )
    out.sort(key=lambda x: (not norm_name(x["nome"]).startswith(norm_name(q)), x["nome"]))
    return out


# ───────────────────────── retrato por pessoa (person_latest) ─────────────────────────


async def snapshots(session: AsyncSession) -> Dict[str, dict]:
    """{fonte: {ano, mes}} do retrato por pessoa (fontes sem retrato não aparecem)."""
    rows = await session.execute(
        select(PersonLatest.fonte, func.max(PersonLatest.ano * 100 + PersonLatest.mes)).group_by(
            PersonLatest.fonte
        )
    )
    return {f: {"ano": int(v) // 100, "mes": int(v) % 100} for f, v in rows.all() if v}


async def refresh_person_latest(session: AsyncSession, fonte: str) -> int:
    """
    Reconstrói o retrato mais recente de uma fonte (apaga e insere na mesma transação).
    Devolve quantas pessoas ficaram no retrato.
    """
    plist = await periods(session, fonte, 1)
    await session.execute(delete(PersonLatest).where(PersonLatest.fonte == fonte))
    if not plist:
        await session.commit()
        return 0
    ano, mes = plist[0]["ano"], int(plist[0]["mes"])
    cols = [
        PersonLatest.fonte, PersonLatest.ident, PersonLatest.matricula, PersonLatest.nome,
        PersonLatest.cargo, PersonLatest.orgao, PersonLatest.cpf, PersonLatest.ano,
        PersonLatest.mes, PersonLatest.valor_bruto, PersonLatest.valor_liquido,
    ]  # fmt: skip
    from sqlalchemy import literal

    if fonte == "DF":
        m = RemunerationCollected
        sel = (
            select(
                literal("DF"),
                m.codigo_identificacao,
                m.codigo_matricula,
                m.nome_servidor,
                m.cargo,
                m.nome_orgao,
                m.cpf_servidor,
                literal(ano),
                literal(mes),
                m.valor_bruto,
                m.valor_liquido,
            )  # fmt: skip
            .where(m.ano_exercicio == ano, m.mes_referencia == f"{mes:02d}")
            .distinct(m.codigo_identificacao, m.codigo_matricula)
            .order_by(m.codigo_identificacao, m.codigo_matricula, m.id)
        )
    elif fonte == "SENADO":
        m = SenadoRemuneracao
        sel = (
            select(
                literal("SENADO"),
                cast(m.sequencial, String(40)),
                literal(None),
                func.max(m.nome),
                func.max(m.cargo),
                func.max(m.lotacao_nome),
                literal(None),
                literal(ano),
                literal(mes),
                func.sum(m.valor_bruto),
                func.sum(m.valor_liquido),
            )  # fmt: skip
            .where(m.ano == ano, m.mes == mes)
            .group_by(m.sequencial)
        )
    else:
        m = FederalRemuneracao
        sel = select(
            literal("FEDERAL"), cast(m.id_servidor_portal, String(40)), m.matricula_mascarada,
            m.nome, m.cargo, m.org_exercicio, m.cpf_mascarado, literal(ano), literal(mes),
            m.valor_bruto, m.valor_liquido,
        ).where(m.ano == ano, m.mes == mes)  # fmt: skip
    await session.execute(insert(PersonLatest).from_select([c.key for c in cols], sel))
    await session.commit()
    total = (
        await session.execute(
            select(func.count()).select_from(PersonLatest).where(PersonLatest.fonte == fonte)
        )
    ).scalar_one()
    return int(total)


# ───────────────────────── perfil da pessoa ─────────────────────────

_DF_COMPONENTS = [
    "valor_remuneracao_basica",
    "valor_beneficios",
    "valor_funcoes",
    "valor_comissao_conselheiro",
    "valor_hora_extra",
    "valor_verbas_eventuais",
    "valor_verbas_judiciais",
    "valor_receitas_meses_anteriores",
    "valor_reposicao_descontos_maior",
    "valor_licenca_premio",
    "valor_imposto_renda",
    "valor_seguridade_social",
    "valor_redutor_teto",
    "valor_descontos_meses_anteriores",
    "valor_reposicao_pagamento_maior",
    "valor_expurgo",
]
_SENADO_COMPONENTS = [
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
    "previdencia",
    "imposto_renda",
    "faltas",
    "reversao_teto_constitucional",
]
_FEDERAL_COMPONENTS = [
    "remuneracao_basica_bruta",
    "abate_teto",
    "gratificacao_natalina",
    "abate_teto_natalina",
    "ferias",
    "outras_eventuais",
    "irrf",
    "pss_rpgs",
    "demais_deducoes",
    "verbas_indenizatorias",
]


def _components(row: Any, names: List[str]) -> Dict[str, float]:
    out = {}
    for name in names:
        value = getattr(row, name, None)
        if value:  # só o que tem valor (a UI não precisa de dezenas de zeros)
            out[name] = float(value)
    return out


def _hist_item(ano: int, mes: int, bruto: float, liquido: float, comps: Dict[str, float]) -> dict:
    return {
        "ano": int(ano),
        "mes": f"{int(mes):02d}",
        "valor_bruto": float(bruto or 0),
        "valor_liquido": float(liquido or 0),
        "componentes": comps,
    }


async def _df_person(session: AsyncSession, ident: str, matricula: Optional[str]) -> Optional[dict]:
    m = RemunerationCollected
    stmt = select(m).where(m.codigo_identificacao == ident)
    if matricula:
        stmt = stmt.where(m.codigo_matricula == matricula)
    rows = (
        (await session.execute(stmt.order_by(m.ano_exercicio, m.mes_referencia).limit(240)))
        .scalars()
        .all()
    )
    if not rows:
        return None
    last = rows[-1]
    return {
        "identidade": {
            "nome": (last.nome_servidor or "").strip(),
            "cpf_mascarado": last.cpf_servidor,
            "matricula": last.codigo_matricula,
            "cargo": last.cargo,
            "funcao": last.funcao,
            "orgao": (last.nome_orgao or "").strip() or None,
            "lotacao": None,
            "orgao_superior": None,
            "situacao": last.situacao_funcional,
            "vinculo": last.situacao_funcional_detalhada,
            "regime": None,
            "jornada": None,
            "uf": "DF",
            "tipo": "Servidor do Governo do DF",
        },
        "historico": [
            _hist_item(
                r.ano_exercicio,
                int(r.mes_referencia),
                r.valor_bruto,
                r.valor_liquido,
                _components(r, _DF_COMPONENTS),
            )
            for r in rows
        ],
        "cargo": last.cargo,
        "id_out": str(last.codigo_identificacao),
    }


async def _senado_person(session: AsyncSession, ident: str) -> Optional[dict]:
    m = SenadoRemuneracao
    try:
        seq = int(ident)
    except ValueError:
        return None
    ref = (
        (
            await session.execute(
                select(m).where(m.sequencial == seq).order_by(m.ano.desc(), m.mes.desc()).limit(1)
            )
        )
        .scalars()
        .first()
    )
    if ref is None:
        return None
    # O sequencial não é estável entre endpoints; o histórico agrupa pelo MESMO nome exato
    rows = (
        (
            await session.execute(
                select(m)
                .where(func.immutable_unaccent(m.nome).ilike(like_exact(ref.nome), escape="\\"))
                .order_by(m.ano, m.mes, m.tipo_folha)
                .limit(240)
            )
        )
        .scalars()
        .all()
    )
    rows = [r for r in rows if norm_name(r.nome) == norm_name(ref.nome)] or [ref]
    # uma linha por competência (soma folha Normal + Suplementar)
    by_period: Dict[tuple, dict] = {}
    for r in rows:
        key = (r.ano, r.mes)
        comps = _components(r, _SENADO_COMPONENTS)
        if key not in by_period:
            by_period[key] = _hist_item(r.ano, r.mes, r.valor_bruto, r.valor_liquido, comps)
        else:
            cur = by_period[key]
            cur["valor_bruto"] += float(r.valor_bruto or 0)
            cur["valor_liquido"] += float(r.valor_liquido or 0)
            for k, v in comps.items():
                cur["componentes"][k] = cur["componentes"].get(k, 0.0) + v
    last = rows[-1]
    return {
        "identidade": {
            "nome": ref.nome.strip(),
            "cpf_mascarado": None,
            "matricula": None,
            "cargo": last.cargo,
            "funcao": last.funcao,
            "orgao": "Senado Federal",
            "lotacao": last.lotacao_nome,
            "orgao_superior": None,
            "situacao": last.situacao,
            "vinculo": last.vinculo,
            "regime": None,
            "jornada": None,
            "uf": "DF",
            "tipo": "Servidor do Senado Federal",
        },
        "historico": [by_period[k] for k in sorted(by_period)],
        "cargo": last.cargo,
        "id_out": str(ident),
    }


async def _federal_person(session: AsyncSession, ident: str) -> Optional[dict]:
    m = FederalRemuneracao
    try:
        pid = int(ident)
    except ValueError:
        return None
    rows = (
        (
            await session.execute(
                select(m).where(m.id_servidor_portal == pid).order_by(m.ano, m.mes).limit(240)
            )
        )
        .scalars()
        .all()
    )
    if not rows:
        return None
    last = rows[-1]
    return {
        "identidade": {
            "nome": last.nome.strip(),
            "cpf_mascarado": last.cpf_mascarado,
            "matricula": last.matricula_mascarada,
            "cargo": last.cargo,
            "funcao": last.funcao,
            "orgao": last.org_exercicio,
            "lotacao": last.org_lotacao,
            "orgao_superior": last.orgao_superior,
            "situacao": last.situacao_vinculo,
            "vinculo": last.tipo_vinculo,
            "regime": last.regime_juridico,
            "jornada": last.jornada,
            "uf": last.uf_exercicio,
            "tipo": "Servidor civil do Executivo federal",
        },
        "historico": [
            _hist_item(
                r.ano, r.mes, r.valor_bruto, r.valor_liquido, _components(r, _FEDERAL_COMPONENTS)
            )
            for r in rows
        ],
        "cargo": last.cargo,
        "id_out": str(pid),
    }


async def peers(
    session: AsyncSession, fonte: str, cargo: Optional[str], ano: int, mes: int, valor: float
):
    """Compara a remuneração com a de quem ocupa o mesmo cargo na mesma competência."""
    if not cargo:
        return None
    s = SPECS[fonte]
    row = (
        await session.execute(
            select(
                func.count(),
                func.avg(s.bruto),
                func.percentile_cont(0.5).within_group(s.bruto),
                func.percentile_cont(0.9).within_group(s.bruto),
                func.count().filter(s.bruto <= valor),
            ).where(s.ano == ano, s.mes == mes, s.cargo == cargo)
        )
    ).one()
    n = int(row[0] or 0)
    if n < 2:
        return None
    return {
        "criterio": "mesmo cargo na mesma competência",
        "cargo": cargo,
        "ano": ano,
        "mes": f"{mes:02d}",
        "n": n,
        "media_bruto": float(row[1] or 0),
        "mediana_bruto": float(row[2] or 0),
        "p90_bruto": float(row[3] or 0),
        "percentil": round(100.0 * int(row[4] or 0) / n, 1),
    }


async def other_sources(
    session: AsyncSession, fonte: str, nome: str, cpf_masked: Optional[str]
) -> List[dict]:
    """
    Cruzamento PROBABILÍSTICO com as outras fontes (retrato mais recente de cada uma), por nome
    exato (sem acento) e, quando as duas fontes trazem o CPF mascarado, pelos 6 dígitos centrais.
    Nunca é confirmação de identidade.
    """
    wanted = norm_name(nome)
    mine = cpf_core(cpf_masked)
    out: List[dict] = []
    for other in SOURCES:
        if other == fonte:
            continue
        rows = (
            await session.execute(
                select(
                    PersonLatest.ident,
                    PersonLatest.nome,
                    PersonLatest.cargo,
                    PersonLatest.orgao,
                    PersonLatest.valor_bruto,
                    PersonLatest.ano,
                    PersonLatest.mes,
                    PersonLatest.cpf,
                )  # fmt: skip
                .where(
                    PersonLatest.fonte == other,
                    func.immutable_unaccent(PersonLatest.nome).ilike(like_exact(nome), escape="\\"),
                )
                .limit(10)
            )
        ).all()
        for row in rows:
            if norm_name(row[1]) != wanted:
                continue
            theirs = cpf_core(row[7]) if other in ("DF", "FEDERAL") else None
            if mine and theirs and mine != theirs:
                continue  # CPFs centrais diferentes: pessoas diferentes
            strong = bool(mine and theirs and mine == theirs)
            out.append(
                {
                    "fonte": other,
                    "id": str(row[0]),
                    "nome": (row[1] or "").strip(),
                    "cargo": row[2],
                    "orgao": ((row[3] or "").strip() or None),
                    "ano": int(row[5]),
                    "mes": f"{int(row[6]):02d}",
                    "valor_bruto": float(row[4] or 0),
                    "confianca": "provavel" if strong else "possivel",
                    "motivo": (
                        "mesmo nome e mesmos 6 dígitos centrais do CPF"
                        if strong
                        else "mesmo nome (sem CPF comparável na fonte); pode ser homônimo"
                    ),
                }
            )
    return out


async def federal_ficha(portal_id: str) -> Optional[dict]:
    """
    Ficha da pessoa na API do portal federal (uma chamada sob demanda; o endpoint cacheia por
    dias). Devolve None sem chave configurada ou em qualquer falha.
    """
    if not settings.portal_federal_api_key:
        return None
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.get(
                f"https://api.portaldatransparencia.gov.br/api-de-dados/servidores/{portal_id}",
                headers={
                    "chave-api-dados": settings.portal_federal_api_key,
                    "Accept": "application/json",
                },
            )
        if r.status_code != 200:
            return None
        data = r.json()
    except Exception:
        return None
    # remove duplicatas de identificação já mostradas no perfil e mantém só o que tem conteúdo
    return {k: v for k, v in data.items() if v not in (None, [], {})}


async def person(
    session: AsyncSession, fonte: str, ident: str, matricula: Optional[str]
) -> Optional[dict]:
    """Perfil completo da pessoa (sem a ficha federal, que é buscada e cacheada à parte)."""
    if fonte == "DF":
        base = await _df_person(session, ident, matricula)
    elif fonte == "SENADO":
        base = await _senado_person(session, ident)
    else:
        base = await _federal_person(session, ident)
    if base is None:
        return None
    hist = base["historico"]
    ultimo = hist[-1] if hist else None
    pares = None
    if ultimo:
        pares = await peers(
            session, fonte, base["cargo"], ultimo["ano"], int(ultimo["mes"]), ultimo["valor_bruto"]
        )
    ident_data = base["identidade"]
    return {
        "fonte": fonte,
        "id": base["id_out"],
        "identidade": ident_data,
        "historico": hist,
        "ultimo": ultimo,
        "pares": pares,
        "outras_fontes": await other_sources(
            session, fonte, ident_data["nome"], ident_data.get("cpf_mascarado")
        ),
        "ficha_federal": None,
    }

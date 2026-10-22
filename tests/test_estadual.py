"""
tests/test_estadual.py
Framework de estados: contrato do adaptador, coletor idempotente, busca, perfil e cruzamento.
Usa um adaptador falso (os adaptadores reais de CE/PE/GO/SC têm testes próprios).
"""

from pathlib import Path
from unittest.mock import patch

import pytest

from app.collector.estadual import EstadualCollector, LoadRejected, _prepare
from app.persistence.repositories import EstadualRepository
from app.services import people
from app.sources import ADAPTERS


class FakeAdapter:
    uf = "CE"
    nome = "Governo do Ceará (teste)"

    def __init__(self, rows_by_month=None, published=True):
        self.rows_by_month = rows_by_month or {}
        self.published = published
        self.calls = 0

    async def is_published(self, ano, mes):
        return self.published

    def iter_rows(self, ano, mes, work_dir: Path):
        self.calls += 1
        yield from self.rows_by_month.get((ano, mes), [])


def row(ident, nome, bruto=1000.0, cargo="ANALISTA", orgao="SEFAZ", **extra):
    return {
        "ident": ident,
        "nome": nome,
        "cargo": cargo,
        "orgao": orgao,
        "valor_bruto": bruto,
        "valor_liquido": bruto * 0.8,
        "valor_descontos": bruto * 0.2,
        "detalhes": {"gratificacao": 100.0},
        **extra,
    }


@pytest.fixture
def estado_ce(monkeypatch):
    """Registra CE como estado ativo só durante o teste."""
    monkeypatch.setattr(people, "SPECS", dict(people.SPECS))
    monkeypatch.setattr(people, "STATE_SOURCES", people.STATE_SOURCES)
    monkeypatch.setattr(people, "SOURCES", people.SOURCES)
    people.register_state("CE")


def test_prepare_normaliza_deduplica_e_descarta_linha_invalida():
    rows = [
        row("1", "  JOSE  "),
        row("1", "  JOSE  "),  # linha idêntica no mesmo mês
        {"ident": "", "nome": "SEM ID"},  # inválida
        {"ident": "9", "nome": ""},  # inválida
        row("2", "MARIA", cargo=None),
    ]
    out = list(_prepare("CE", 2026, 8, iter(rows)))
    # cópia exata é mantida (homônimos idênticos são pessoas distintas), com hash distinto
    assert [r["ident"] for r in out] == ["1", "1", "2"]
    assert len({r["linha_hash"] for r in out}) == 3
    assert out[0]["nome"] == "JOSE" and out[0]["uf"] == "CE" and len(out[0]["linha_hash"]) == 32
    assert out[2]["cargo"] is None and out[0]["valor_bruto"] == 1000.0


@pytest.mark.asyncio
async def test_coletor_estadual_idempotente_e_pula_mes_carregado(db_session):
    adapter = FakeAdapter({(2081, 4): [row("1", "ANA TESTE"), row("2", "BRUNO TESTE", 5000.0)]})
    repo = EstadualRepository(db_session, "CE")
    collector = EstadualCollector(adapter, repo)

    first = await collector.collect_month(2081, 4)
    assert first == {"uf": "CE", "ano": 2081, "mes": 4, "registros": 2, "status": "success"}

    # segunda chamada: já carregado, a fonte nem é consultada
    again = await collector.collect_month(2081, 4)
    assert again["status"] == "already_loaded" and adapter.calls == 1

    # forçar substitui sem duplicar
    forced = await collector.collect_month(2081, 4, force=True)
    assert forced["registros"] == 2
    items, total = await repo.search(ano=2081, mes="4", limit=10, offset=0)
    assert total == 2 and {i["fonte"] for i in items} == {"CE"}

    adapter.published = False
    assert (await collector.collect_month(2081, 5))["status"] == "empty"


@pytest.mark.asyncio
async def test_estados_ficam_separados_na_tabela_unica(db_session):
    repo_ce = EstadualRepository(db_session, "CE")
    repo_pe = EstadualRepository(db_session, "PE")
    await repo_ce.replace_month(2080, 1, _prepare("CE", 2080, 1, iter([row("1", "NOME CE")])))
    await repo_pe.replace_month(2080, 1, _prepare("PE", 2080, 1, iter([row("1", "NOME PE")])))
    await repo_ce.replace_month(2080, 1, _prepare("CE", 2080, 1, iter([row("2", "OUTRO CE")])))
    _, total_pe = await repo_pe.search(ano=2080, limit=10, offset=0)
    items_ce, total_ce = await repo_ce.search(ano=2080, limit=10, offset=0)
    assert total_pe == 1  # recarregar o CE não mexe no PE
    assert total_ce == 1 and items_ce[0]["nome_servidor"] == "OUTRO CE"


@pytest.mark.usefixtures("override_auth")
@pytest.mark.asyncio
async def test_endpoints_para_estado_busca_stats_perfil_e_cruzamento(
    client, db_session, override_get_session, estado_ce
):
    from app.collector.senado import build_record
    from app.persistence.repositories import SenadoRepository

    repo = EstadualRepository(db_session, "CE")
    # duas competências da mesma pessoa + outra pessoa
    for mes, bruto in ((1, 1000.0), (2, 1200.0)):
        await repo.replace_month(
            2079,
            mes,
            _prepare(
                "CE",
                2079,
                mes,
                iter([row("P1", "CLAUDIA CEARENSE", bruto), row("P2", "DANIEL CEARENSE", 3000.0)]),
            ),
        )
    await SenadoRepository(db_session).replace_month(
        2079,
        2,
        [
            build_record(
                {"sequencial": 11, "nome": "CLAUDIA CEARENSE", "tipo_folha": "Normal"},
                None,
                2079,
                2,
            )
        ],
    )
    for fonte in ("CE", "SENADO"):
        await people.refresh_person_latest(db_session, fonte)

    r = await client.get("/api/v1/remuneration/?fonte=CE&nome=claudia&ano=2079")
    assert r.status_code == 200 and r.json()["items"][0]["fonte"] == "CE"
    assert r.json()["total"] == 1  # retrato por pessoa: uma linha

    periods = (await client.get("/api/v1/remuneration/periods")).json()
    assert {"ano": 2079, "mes": "02"} in periods["CE"]

    stats = (await client.get("/api/v1/remuneration/stats?fonte=CE&ano=2079")).json()
    assert stats["total_servidores"] == 2 and len(stats["mensal"]) == 2

    person = (await client.get("/api/v1/remuneration/person?fonte=CE&id=P1")).json()
    assert person["identidade"]["nome"] == "CLAUDIA CEARENSE" and person["identidade"]["uf"] == "CE"
    assert [h["mes"] for h in person["historico"]] == ["01", "02"]  # histórico por ident, 2 meses
    assert person["ultimo"]["valor_bruto"] == 1200.0
    assert "gratificacao" in person["ultimo"]["componentes"]
    assert any(
        o["fonte"] == "SENADO" and o["confianca"] == "possivel" for o in person["outras_fontes"]
    )

    csv = await client.get("/api/v1/remuneration/export?fonte=CE&nome=claudia&ano=2079")
    assert csv.status_code == 200 and "CLAUDIA" in csv.content.decode("utf-8-sig")


def test_registro_de_adaptadores_ignora_modulos_ausentes():
    """Módulos de estados ainda não entregues não derrubam o carregamento do registro."""
    from app.sources import load_adapters

    assert isinstance(load_adapters(), dict)
    assert set(load_adapters()) <= {"CE", "PE", "GO", "SC"}
    assert isinstance(ADAPTERS, dict)
    with patch("app.sources.MODULES", ("nao_existe",)):
        assert load_adapters() == {}


@pytest.mark.asyncio
async def test_carga_vazia_nao_apaga_o_mes_existente(db_session):
    repo = EstadualRepository(db_session, "CE")
    await EstadualCollector(FakeAdapter({(2081, 5): [row("1", "ANA"), row("2", "BRUNO")]}), repo).collect_month(2081, 5)

    with pytest.raises(LoadRejected):
        await EstadualCollector(FakeAdapter({(2081, 5): []}), repo).collect_month(2081, 5, force=True)
    assert await repo.month_count(2081, 5) == 2


@pytest.mark.asyncio
async def test_carga_truncada_e_rejeitada_a_menos_que_aceite_queda(db_session):
    repo = EstadualRepository(db_session, "CE")
    full = [row(str(i), f"PESSOA {i}") for i in range(10)]
    await EstadualCollector(FakeAdapter({(2081, 6): full}), repo).collect_month(2081, 6)

    short = EstadualCollector(FakeAdapter({(2081, 6): full[:3]}), repo)
    with pytest.raises(LoadRejected):
        await short.collect_month(2081, 6, force=True)
    assert await repo.month_count(2081, 6) == 10

    result = await short.collect_month(2081, 6, force=True, accept_shrink=True)
    assert result["registros"] == 3 and await repo.month_count(2081, 6) == 3

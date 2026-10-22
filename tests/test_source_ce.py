"""
tests/test_source_ce.py
Adaptador do Ceará, sem rede (respx). Nomes fictícios.
"""

import httpx
import pytest
import respx

from app.sources import ce
from app.sources.ce import CearaAdapter, CearaFetchError, make_ident, parse_money

URL = ce.BASE_URL


@pytest.fixture(autouse=True)
def sem_pausa(monkeypatch):
    sleeps = []
    monkeypatch.setattr(ce.time, "sleep", lambda s: sleeps.append(s))
    return sleeps


def rec(nome, orgao="220001", bruto="1000.00", **extra):
    base = {
        "id": 1,
        "nome_servidor": nome,
        "total_proventos": bruto,
        "proventos_liquidos": "800.00",
        "diarias": "0.0",
        "total_descontos": "200.00",
        "desconto_teto": "0.0",
        "outros_descontos": "200.00",
        "competencia": "2026-08-01",
        "situacao_funcional": "ATIVO",
        "dsc_cargo": "ANALISTA",
        "codigo_orgao": orgao,
        "descricao_orgao": "SECRETARIA TESTE",
        "sigla": "SECT",
    }
    base.update(extra)
    return base


def page(records, n=1, total_pages=1, total_records=None):
    return {
        "sumary": {
            "total_pages": total_pages,
            "current_page": str(n),
            "total_records": len(records) if total_records is None else total_records,
            "records_per_page": 100,
        },
        "data": records,
    }


def rows(adapter, ano=2026, mes=8):
    return list(adapter.iter_rows(ano, mes, None))


@respx.mock
def test_paginacao_tres_paginas_e_normalizacao():
    route = respx.get(URL)
    route.side_effect = [
        httpx.Response(200, json=page([rec("ANA TESTE")], 1, 3, 3)),
        httpx.Response(200, json=page([rec("BRUNO TESTE", diarias="50.5")], 2, 3, 3)),
        httpx.Response(200, json=page([rec("CARLA TESTE")], 3, 3, 3)),
    ]
    out = rows(CearaAdapter())
    assert [r["nome"] for r in out] == ["ANA TESTE", "BRUNO TESTE", "CARLA TESTE"]
    assert [c.request.url.params["page"] for c in route.calls] == ["1", "2", "3"]
    assert route.calls[0].request.url.params["month"] == "8"
    r = out[1]
    assert (
        r["valor_bruto"] == 1000.0 and r["valor_liquido"] == 800.0 and r["valor_descontos"] == 200.0
    )
    assert r["detalhes"] == {"diarias": 50.5, "outros_descontos": 200.0}  # zeros omitidos
    assert (
        r["orgao"] == "SECRETARIA TESTE" and r["cargo"] == "ANALISTA" and r["situacao"] == "ATIVO"
    )
    assert "cpf_mascarado" not in r and "matricula" not in r


@respx.mock
def test_pausa_minima_entre_requisicoes(sem_pausa, monkeypatch):
    ticks = iter(range(0, 1000))
    monkeypatch.setattr(ce.time, "monotonic", lambda: next(ticks) * 0.01)  # sempre < 0.6s
    respx.get(URL).side_effect = [
        httpx.Response(200, json=page([rec("ANA TESTE")], 1, 2, 2)),
        httpx.Response(200, json=page([rec("BRUNO TESTE")], 2, 2, 2)),
    ]
    rows(CearaAdapter())
    assert sem_pausa and all(s >= 0.5 for s in sem_pausa)


@respx.mock
@pytest.mark.asyncio
async def test_is_published_true_e_mes_sem_dados():
    respx.get(URL, params={"year": 2026, "month": 8, "page": 1}).respond(
        200, json=page([rec("ANA TESTE")], 1, 1, 1)
    )
    respx.get(URL, params={"year": 2099, "month": 1, "page": 1}).respond(
        200,
        json={"sumary": {"total_pages": 0, "current_page": "1", "total_records": 0}, "data": []},
    )
    respx.get(URL, params={"year": 2098, "month": 1, "page": 1}).respond(500)
    adapter = CearaAdapter()
    assert await adapter.is_published(2026, 8) is True
    assert await adapter.is_published(2099, 1) is False
    assert await adapter.is_published(2098, 1) is False


@respx.mock
def test_campos_numericos_como_texto_e_numero():
    respx.get(URL).respond(
        200,
        json=page(
            [
                rec("ANA TESTE", bruto="1.234,56", total_descontos="12,5"),
                rec("BRUNO TESTE", bruto=2500.5, proventos_liquidos="lixo", total_descontos=None),
            ]
        ),
    )
    a, b = rows(CearaAdapter())
    assert a["valor_bruto"] == 1234.56 and a["valor_descontos"] == 12.5
    assert b["valor_bruto"] == 2500.5 and b["valor_liquido"] == 0.0 and b["valor_descontos"] == 0.0
    assert (
        parse_money("1234.56") == 1234.56 and parse_money("") == 0.0 and parse_money("nan") == 0.0
    )


@respx.mock
def test_ident_estavel_entre_meses_e_distinto_entre_orgaos():
    respx.get(URL, params={"year": 2026, "month": 7, "page": 1}).respond(
        200, json=page([rec("João da Síl-va", "100"), rec("JOAO DA SIL-VA", "200")])
    )
    respx.get(URL, params={"year": 2026, "month": 8, "page": 1}).respond(
        200, json=page([rec("  joao da sil-va ", "100", id=999)])
    )
    jul = rows(CearaAdapter(), mes=7)
    ago = rows(CearaAdapter(), mes=8)
    assert jul[0]["ident"] == ago[0]["ident"] == make_ident("JOAO DA SIL-VA", "100")
    assert jul[0]["ident"] != jul[1]["ident"]  # homônimos de órgãos diferentes
    assert len(jul[0]["ident"]) == 16


@respx.mock
def test_429_com_retry_after_e_depois_sucesso(sem_pausa):
    route = respx.get(URL)
    route.side_effect = [
        httpx.Response(429, headers={"Retry-After": "7"}),
        httpx.Response(503, headers={"Retry-After": "9999"}),
        httpx.Response(200, json=page([rec("ANA TESTE")])),
    ]
    out = rows(CearaAdapter())
    assert len(out) == 1 and route.call_count == 3
    assert 7.0 in sem_pausa and 120.0 in sem_pausa  # respeita e limita o Retry-After


@respx.mock
def test_falha_definitiva_levanta_erro_em_vez_de_pular():
    route = respx.get(URL)
    route.side_effect = [
        httpx.Response(200, json=page([rec("ANA TESTE")], 1, 2, 2)),
        *[httpx.Response(500)] * 5,
    ]
    gen = CearaAdapter().iter_rows(2026, 8, None)
    assert next(gen)["nome"] == "ANA TESTE"
    with pytest.raises(CearaFetchError):
        next(gen)
    assert route.call_count == 1 + ce.MAX_ATTEMPTS


@respx.mock
def test_erro_de_rede_tambem_e_tentado_de_novo():
    route = respx.get(URL)
    route.side_effect = [
        httpx.ConnectError("x"),
        httpx.Response(200, json=page([rec("ANA TESTE")])),
    ]
    assert len(rows(CearaAdapter())) == 1


def test_adapter_registrado_no_contrato():
    assert ce.ADAPTER.uf == "CE" and ce.ADAPTER.nome == "Governo do Ceará"

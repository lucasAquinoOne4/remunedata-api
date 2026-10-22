"""
tests/test_source_go.py
Adaptador de Goiás (CKAN) sem rede: respx + fixtures sintéticas com nomes fictícios.
"""

import asyncio

import httpx
import pytest
import respx

from app.sources import go
from app.sources.go import ADAPTER, SEARCH_URL, PACKAGE_URL, GoSourceError, make_ident, _num

CSV_URL = "https://dadosabertos.go.gov.br/dataset/x/resource/r-ago/download/folhapagamento_202608.csv"


def package(ago_ds=True):
    def res(rid, name, fname, ds=True, fmt="CSV"):
        return {
            "id": rid, "name": name, "format": fmt, "datastore_active": ds,
            "url": f"https://dadosabertos.go.gov.br/dataset/x/resource/{rid}/download/{fname}",
        }  # fmt: skip

    return {
        "success": True,
        "result": {
            "resources": [
                res("dict", "Dicionário de Dados", "dicionario.pdf", False, "PDF"),
                res("r-ago", "Folha de Pagamento - Agosto/2026", "folhapagamento_202608.csv", ago_ds),
                res("r-jul", "Folha de Pagamento - Julho/2026", "folhapagamento_202607.csv"),
                res("z25", "Folha de Pagamento - 2025", "folhapagamento_ano_2025.zip", False, "ZIP"),
            ]
        },
    }


def rec(i, nome="ANA FICTICIA", cod="309", **kw):
    base = {
        "_id": i, "CODORGAO": cod, "ANOMES": "202608", "NOMESERVIDOR": nome,
        "NOMECARGO": "Agente", "ORGAO": "ORGAO TESTE", "VALORPROVENTO": "3534,06",
        "VALORPROVENTOMES": "3534,06", "VALORLIQUIDO": "1554,01", "VALORFERIAS": "0",
        "VALORDECIMOTERCEIRO": "0", "VALORDEMAISDESCONTOS": "1980,05", "VALORCORTETETO": "0",
    }  # fmt: skip
    base.update(kw)
    return base


def page(records, total):
    return httpx.Response(200, json={"success": True, "result": {"records": records, "total": total}})


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(go, "PAUSE", 0)
    monkeypatch.setattr(go, "BACKOFF", 0)
    monkeypatch.setattr(go, "PAGE_SIZE", 2)


def mock_pkg(m, **kw):
    m.get(PACKAGE_URL).mock(return_value=httpx.Response(200, json=package(**kw)))


def test_num_aceita_virgula_ponto_e_lixo():
    assert _num("1.234,56") == 1234.56
    assert _num("1234,5") == 1234.5
    assert _num("1234.5") == 1234.5
    assert _num("") == 0.0 and _num(None) == 0.0 and _num("abc") == 0.0


def test_ident_estavel_sem_acento_e_caixa():
    assert make_ident("José  da Silva", "309") == make_ident("JOSE DA SILVA", " 309")
    assert make_ident("JOSE DA SILVA", "309") != make_ident("JOSE DA SILVA", "310")
    assert len(make_ident("X", "1")) == 16


@respx.mock
def test_escolhe_recurso_do_mes_e_pagina_em_3_paginas():
    mock_pkg(respx)
    route = respx.get(SEARCH_URL).mock(
        side_effect=[
            page([rec(1, "ANA FICTICIA"), rec(2, "BRUNO FICTICIO")], 5),
            page([rec(3, "CARLA FICTICIA"), rec(4, "DANIEL FICTICIO")], 5),
            page([rec(5, "ELIANA FICTICIA")], 5),
        ]
    )
    rows = list(ADAPTER.iter_rows(2026, 8, None))
    assert len(rows) == 5 and route.call_count == 3
    assert all(c.request.url.params["resource_id"] == "r-ago" for c in route.calls)
    assert [c.request.url.params["offset"] for c in route.calls] == ["0", "2", "4"]
    r = rows[0]
    assert r["valor_bruto"] == 3534.06 and r["valor_liquido"] == 1554.01
    assert r["valor_descontos"] == 1980.05
    assert r["detalhes"] == {"demais_descontos": 1980.05}  # só os não zerados
    assert r["cargo"] == "Agente" and r["orgao"] == "ORGAO TESTE"


@respx.mock
def test_detalhes_e_provento_mes_diferente():
    mock_pkg(respx)
    respx.get(SEARCH_URL).mock(
        return_value=page([rec(1, VALORFERIAS="1.000,50", VALORPROVENTO="4534,56")], 1)
    )
    (r,) = list(ADAPTER.iter_rows(2026, 8, None))
    assert r["valor_bruto"] == 4534.56
    assert r["detalhes"]["ferias"] == 1000.5 and r["detalhes"]["provento_mes"] == 3534.06


@respx.mock
def test_ident_estavel_entre_meses():
    mock_pkg(respx)
    respx.get(SEARCH_URL).mock(
        side_effect=[page([rec(1, "Ana Ficticia")], 1), page([rec(9, "ANA FICTICIA")], 1)]
    )
    a = list(ADAPTER.iter_rows(2026, 8, None))[0]["ident"]
    b = list(ADAPTER.iter_rows(2026, 7, None))[0]["ident"]
    assert a == b


@respx.mock
def test_fallback_csv_quando_sem_datastore(tmp_path):
    mock_pkg(respx, ago_ds=False)
    csv_text = (
        "ANOMES;CODORGAO;ORGAO;NOMESERVIDOR;NOMECARGO;VALORPROVENTO;VALORLIQUIDO;"
        "VALORDEMAISDESCONTOS\n202608;309;ORGAO TESTE;ANA FICTICIA;Agente;3.534,06;1554,01;1980,05\n"
    )
    respx.get(CSV_URL).mock(return_value=httpx.Response(200, content=csv_text.encode("utf-8-sig")))
    rows = list(ADAPTER.iter_rows(2026, 8, tmp_path))
    assert len(rows) == 1 and rows[0]["valor_bruto"] == 3534.06
    assert list(tmp_path.iterdir()) == []  # CSV apagado


@respx.mock
def test_mes_inexistente_e_anterior_a_2026():
    mock_pkg(respx)
    assert asyncio.run(ADAPTER.is_published(2026, 6)) is False  # junho/2026 ausente
    assert asyncio.run(ADAPTER.is_published(2025, 3)) is False  # só ZIP anual
    with pytest.raises(GoSourceError):
        list(ADAPTER.iter_rows(2026, 6, None))


@respx.mock
def test_is_published_true_com_datastore_e_false_se_vazio():
    mock_pkg(respx)
    respx.get(SEARCH_URL).mock(side_effect=[page([{"_id": 1}], 9), page([], 0)])
    assert asyncio.run(ADAPTER.is_published(2026, 8)) is True
    assert asyncio.run(ADAPTER.is_published(2026, 8)) is False


@respx.mock
def test_429_recupera_e_erro_definitivo_levanta():
    mock_pkg(respx)
    respx.get(SEARCH_URL).mock(
        side_effect=[httpx.Response(429), httpx.Response(503), page([rec(1)], 1)]
    )
    assert len(list(ADAPTER.iter_rows(2026, 8, None))) == 1

    respx.get(SEARCH_URL).mock(return_value=httpx.Response(500))
    with pytest.raises(GoSourceError):
        list(ADAPTER.iter_rows(2026, 8, None))


def test_registrado_no_registro_de_adaptadores():
    from app.sources import ADAPTERS

    assert ADAPTERS["GO"].nome == "Governo de Goiás"

"""
tests/test_source_sc.py
Adaptador de Santa Catarina (CKAN): sem rede (respx), CSV sintéticos com nomes fictícios.
"""

import hashlib

import httpx
import pytest
import respx

from app.sources import sc
from app.sources.sc import ADAPTER, SCSourceError, parse_decimal

HEADER = (
    "Nome;CPF;Cargo;OrgaoExercicio;UnidadeExercicio;RegimeTrabalho;"
    "CargaHorariaCargo;CargaHorariaServidor;Situacao;ValorBruto"
)
URL_A = "https://dados.sc.gov.br/dl/ativos-2026-09.csv"
URL_A_OLD = "https://dados.sc.gov.br/dl/ativos-2026-09-antigo.csv"
URL_I = "https://dados.sc.gov.br/dl/inativos-2026-09.csv"


def res(name, url, modified="2026-10-08T10:00:00", fmt="CSV"):
    return {"name": name, "url": url, "format": fmt, "last_modified": modified}


def package(resources):
    return {"success": True, "result": {"resources": resources}}


STANDARD = package(
    [
        res("Servidores-Ativos-2026-09.json", "https://x/a.json", fmt="JSON"),
        res("Servidores-Ativos-2026-08.csv", "https://x/ativos-08.csv"),
        res("Servidores-Ativos-2026-09.csv", URL_A_OLD, "2026-10-01T10:00:00"),
        res("Servidores-Ativos-2026-09.csv", URL_A, "2026-10-08T10:00:00"),
        res("Servidores-Inativos-2026-09.csv", URL_I, "2026-10-05T10:00:00"),
    ]
)


@pytest.fixture(autouse=True)
def sem_pausa(monkeypatch):
    monkeypatch.setattr(sc, "_PAUSE", 0)
    monkeypatch.setattr(sc.time, "sleep", lambda s: None)


def csv_bytes(lines, header=HEADER, encoding="utf-8", bom=False):
    text = "\r\n".join([header, *lines]) + "\r\n"
    data = text.encode(encoding)
    return (b"\xef\xbb\xbf" + data) if bom else data


ATIVOS = [
    "ANA FICTICIA;***.111.222-**;ANALISTA;1401-SAUDE;UNID A;ESTATUTARIO;40;30;ATIVO;1234,56",
    "BRUNO TESTE;***.333.444-**;PROFESSOR;2201-EDUCACAO;UNID B;CLT;20;20;ATIVO;8.000,10",
]
INATIVOS = [
    "CARLA SINTETICA;***.555.666-**;AUDITOR;1401-SAUDE;UNID C;ESTATUTARIO;40;40;INATIVO;9000.50",
    "DIOGO PENSAO;***.777.888-**;;1401-SAUDE;;PENSIONISTA DO IPREV;;;PENSIONISTA PREVIDENCIARIO;3000,00",
]


def mock_site(router, ativos=None, inativos=None, pkg=STANDARD):
    router.get(sc.PACKAGE_URL).mock(return_value=httpx.Response(200, json=pkg))
    if ativos is not None:
        router.get(URL_A).mock(return_value=httpx.Response(200, content=ativos))
    if inativos is not None:
        router.get(URL_I).mock(return_value=httpx.Response(200, content=inativos))


def test_identidade_do_adaptador():
    assert ADAPTER.uf == "SC" and ADAPTER.nome == "Governo de Santa Catarina"


def test_escolhe_recurso_mais_recente_entre_duplicatas():
    chosen = sc.pick_resources(STANDARD, 2026, 9)
    assert chosen["ativos"]["url"] == URL_A and chosen["inativos"]["url"] == URL_I
    assert sc.pick_resources(STANDARD, 2026, 8).keys() == {"ativos"}
    assert sc.pick_resources(STANDARD, 2025, 1) == {}  # JSON/XLSX e outros meses não contam


@pytest.mark.asyncio
async def test_is_published():
    with respx.mock() as router:
        mock_site(router)
        assert await ADAPTER.is_published(2026, 9) is True
        assert await ADAPTER.is_published(2026, 10) is False
    with respx.mock() as router:
        # só inativos publicados: ainda não vale como publicado
        router.get(sc.PACKAGE_URL).mock(
            return_value=httpx.Response(
                200, json=package([res("Servidores-Inativos-2026-09.csv", URL_I)])
            )
        )
        assert await ADAPTER.is_published(2026, 9) is False
    with respx.mock() as router:
        router.get(sc.PACKAGE_URL).mock(return_value=httpx.Response(503))
        assert await ADAPTER.is_published(2026, 9) is False


def test_une_ativos_e_inativos_e_normaliza(tmp_path):
    with respx.mock() as router:
        mock_site(router, csv_bytes(ATIVOS), csv_bytes(INATIVOS))
        rows = list(ADAPTER.iter_rows(2026, 9, tmp_path))
    assert len(rows) == 4
    ana = rows[0]
    assert ana["nome"] == "ANA FICTICIA" and ana["cpf_mascarado"] == "***.111.222-**"
    assert ana["cargo"] == "ANALISTA" and ana["orgao"] == "1401-SAUDE"
    assert ana["vinculo"] == "ESTATUTARIO" and ana["situacao"] == "ATIVO"
    assert ana["valor_bruto"] == 1234.56 and ana["valor_liquido"] == 0.0
    assert ana["detalhes"] == {"carga_horaria_cargo": 40.0, "carga_horaria_servidor": 30.0}
    assert rows[1]["valor_bruto"] == 8000.10
    assert rows[2]["situacao"] == "INATIVO" and rows[2]["valor_bruto"] == 9000.50
    assert rows[3]["situacao"] == "PENSIONISTA PREVIDENCIARIO" and rows[3]["cargo"] is None
    assert rows[3]["detalhes"] == {}
    assert list(tmp_path.iterdir()) == []  # arquivos baixados foram apagados


def test_mes_so_com_ativos_funciona(tmp_path):
    pkg = package([res("Servidores-Ativos-2026-09.csv", URL_A)])
    with respx.mock() as router:
        mock_site(router, csv_bytes(ATIVOS), pkg=pkg)
        assert len(list(ADAPTER.iter_rows(2026, 9, tmp_path))) == 2


def test_situacao_vazia_usa_a_do_recurso(tmp_path):
    linha = "EVA TESTE;***.999.000-**;AUX;ORG;U;CLT;;;;100,00"
    with respx.mock() as router:
        mock_site(router, csv_bytes([linha]), csv_bytes([linha.replace("EVA", "FABIO")]))
        rows = list(ADAPTER.iter_rows(2026, 9, tmp_path))
    assert [r["situacao"] for r in rows] == ["ATIVO", "INATIVO"]


def test_bom_e_encoding_cp1252(tmp_path):
    linha = "JOSÉ AÇAÍ;***.111.222-**;TÉCNICO;SECRETARIA DA EDUCAÇÃO;U;CLT;20;20;ATIVO;100,00"
    with respx.mock() as router:
        mock_site(router, csv_bytes([linha], bom=True), csv_bytes([linha], encoding="cp1252"))
        rows = list(ADAPTER.iter_rows(2026, 9, tmp_path))
    assert rows[0]["nome"] == "JOSÉ AÇAÍ" and rows[0]["cargo"] == "TÉCNICO"  # BOM removido
    assert rows[1]["nome"] == "JOSÉ AÇAÍ" and rows[1]["orgao"] == "SECRETARIA DA EDUCAÇÃO"
    assert rows[0]["ident"] == rows[1]["ident"]  # mesma pessoa em qualquer encoding


def test_linha_curta_e_linha_em_branco_toleradas(tmp_path):
    lines = ["FULANA CURTA;***.111.222-**;CARGO", "", ";;;;;;;;;", ATIVOS[0]]
    with respx.mock() as router:
        mock_site(router, csv_bytes(lines), csv_bytes([]))
        rows = list(ADAPTER.iter_rows(2026, 9, tmp_path))
    assert [r["nome"] for r in rows] == ["FULANA CURTA", "ANA FICTICIA"]
    assert rows[0]["valor_bruto"] == 0.0 and rows[0]["orgao"] is None


def test_coluna_essencial_ausente_gera_erro_claro(tmp_path):
    header = "Nome;CPF;Cargo;OrgaoExercicio;Situacao;Salario"
    with respx.mock() as router:
        mock_site(router, csv_bytes(["A;B;C;D;E;1,00"], header=header))
        with pytest.raises(SCSourceError, match="valorbruto"):
            list(ADAPTER.iter_rows(2026, 9, tmp_path))
    assert list(tmp_path.iterdir()) == []


def test_colunas_localizadas_por_nome_normalizado(tmp_path):
    header = "NOME;cpf;CARGO;ÓRGÃO EXERCÍCIO;x;Regime Trabalho;a;b;situação;Valor Bruto"
    # 'ÓRGÃO EXERCÍCIO' normaliza para 'orgaoexercicio'; 'Valor Bruto' para 'valorbruto'
    with respx.mock() as router:
        mock_site(
            router,
            csv_bytes(["ZE TESTE;***.1.2-**;C;ORG;u;CLT;;;ATIVO;5,5"], header=header),
            csv_bytes([]),
        )
        rows = list(ADAPTER.iter_rows(2026, 9, tmp_path))
    assert (
        rows[0]["orgao"] == "ORG" and rows[0]["valor_bruto"] == 5.5 and rows[0]["vinculo"] == "CLT"
    )


def test_mes_nao_publicado_levanta_erro(tmp_path):
    with respx.mock() as router:
        mock_site(router)
        with pytest.raises(SCSourceError, match="não publicado"):
            list(ADAPTER.iter_rows(2026, 10, tmp_path))


def test_ident_estavel_e_distinto_para_homonimos(tmp_path):
    a = "MARIA HOMONIMA;***.123.456-**;ANALISTA;1401-SAUDE;U;CLT;;;ATIVO;1000,00"
    b = "MARIA HOMONIMA;***.123.456-**;ANALISTA;2201-EDUCACAO;U;CLT;;;ATIVO;1000,00"
    c = "Maria  Homônima;***.123.456-**;ANALISTA;1401-Saúde;U;CLT;;;ATIVO;2500,00"
    with respx.mock() as router:
        mock_site(router, csv_bytes([a, b, c]), csv_bytes([]))
        r1, r2, r3 = list(ADAPTER.iter_rows(2026, 9, tmp_path))
    assert r1["ident"] != r2["ident"]  # órgãos distintos
    assert r1["ident"] == r3["ident"]  # acento/caixa/espaços e valor não mudam a chave
    assert r1["ident"] == hashlib.md5(b"MARIA HOMONIMA|123456|1401-SAUDE").hexdigest()[:16]
    assert len(r1["ident"]) == 16


@pytest.mark.parametrize(
    "raw,esperado",
    [
        ("16394,92", 16394.92),
        ("16394.92", 16394.92),
        ("1.234,56", 1234.56),
        ("1,234.56", 1234.56),
        ("R$ 1.000,00", 1000.0),
        ("1.234.567,89", 1234567.89),
        ("1.234", 1234.0),
        ("7", 7.0),
        ("-12,5", -12.5),
        ("", None),
        ("abc", None),
        (None, None),
    ],
)
def test_parse_decimal(raw, esperado):
    assert parse_decimal(raw) == esperado

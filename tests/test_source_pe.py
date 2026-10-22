"""
tests/test_source_pe.py
Adaptador de Pernambuco (app/sources/pe.py): sem rede, com respx e CSVs sintéticos (nomes fictícios).
"""

import hashlib

import httpx
import pytest
import respx

from app.sources import base, pe

HEADER = (
    "r_instituicao;r_cpf;r_matricula;r_nome;r_categoria;r_cargo;r_vencimento_cargo;r_funcao;"
    "r_gratificacao_funcao;r_remuneracao;r_ferias;r_natalina;r_outras_vantagens;r_total_vantagens;"
    "r_desconto_excedente;r_descontos_faltas;r_descontos_previdencia;r_imposto_renda;"
    "r_descontos_compulsorios;r_valor_liquido;r_outros_creditos"
)
ATIVO_1 = (
    "SEC TESTE;***.111.222-**;1020781/03;FULANO DE TESTE;SERVIDOR EFETIVO;ANALISTA;10230.71;NA;"
    "0;11765.32;0;0;4646.52;16411.84;0;0;1432.3;2326.74;3759.04;12652.8;"
)
ATIVO_2 = (
    "SEC TESTE;***.333.444-**;;BELTRANA EXEMPLO;EXTRA QUADRO INTERNO;NA;0;FG 2;"
    "558.47;1893.21;0;0;0;1893.21;0;0;0;0;0;1893.21;"
)
INATIVO_1 = (
    "FUNDO TESTE;***.555.666-**;77/01;CICRANO SINTETICO;APOSENTADO;;5000;NA;0;5000;0;0;0;5000;"
    "0;0;0;300;300;4700;"
)


def csv_text(*lines, header=HEADER):
    return "\r\n".join([header, *lines]) + "\r\n"


def res(tipo, ano, mes, rid, created="2026-10-15T10:00:00", size=100, fmt="CSV", name=None):
    fname = f"{ano}_{mes}_remuneracao_{tipo}.{fmt.lower()}"
    return {
        "name": name or f"Remuneração {tipo} {mes:02d}/{ano}",
        "format": fmt,
        "state": "active",
        "created": created,
        "size": size,
        "url": f"https://dados.pe.gov.br/dataset/x/resource/{rid}/download/{fname}",
    }


@pytest.fixture(autouse=True)
def sem_pausa(monkeypatch):
    monkeypatch.setattr(pe, "_PAUSE", 0.0)


def mock_package(router, resources):
    router.get(pe.PACKAGE_URL).mock(
        return_value=httpx.Response(200, json={"success": True, "result": {"resources": resources}})
    )


# ------------------------------------------------------------------ recursos


def test_escolhe_recurso_do_mes_e_resolve_duplicata_pelo_mais_recente():
    resources = [
        res("ativos", 2026, 8, "a", created="2026-09-10T00:00:00"),
        res("ativos", 2026, 8, "b", created="2026-09-20T00:00:00", size=1),  # republicado: vence
        res("ativos", 2026, 8, "json", fmt="JSON", created="2026-10-30T00:00:00"),
        res("inativos", 2026, 8, "c"),
        res("ativos", 2026, 9, "d"),
        res("ativos", 2025, 13, "e", name="Remuneração ativos 013/2025"),
    ]
    assert pe.pick_resource(resources, "ativos", 2026, 8)["url"].split("/")[-3] == "b"
    assert pe.pick_resource(resources, "inativos", 2026, 8)["url"].split("/")[-3] == "c"
    assert pe.pick_resource(resources, "ativos", 2026, 7) is None
    assert pe.pick_resource(resources, "ativos", 2025, 12) is None  # mês 13 (13º) não vira dezembro


def test_periodo_por_nome_zero_a_esquerda_e_fallback_pela_url():
    assert pe.resource_period({"format": "CSV", "name": "Remuneração ativos 012/2022 (novo)"}) == (
        "ativos",
        2022,
        12,
    )
    assert pe.resource_period({"format": "CSV", "name": "Remuneração inativos 09/2026"}) == (
        "inativos",
        2026,
        9,
    )
    sem_nome = {"format": ".csv", "name": "x", "url": "https://h/2016_4_remuneracao_ativos-1.csv"}
    assert pe.resource_period(sem_nome) == ("ativos", 2016, 4)
    assert pe.resource_period({"format": "JSON", "name": "Remuneração ativos 01/2026"}) is None


@pytest.mark.asyncio
async def test_is_published_verdadeiro_e_falso():
    with respx.mock() as router:
        mock_package(router, [res("ativos", 2026, 8, "a"), res("inativos", 2026, 9, "b")])
        assert await pe.ADAPTER.is_published(2026, 8) is True
        assert await pe.ADAPTER.is_published(2026, 9) is False  # só inativos não basta
        assert await pe.ADAPTER.is_published(2026, 10) is False


# ------------------------------------------------------------------ linhas


def run(tmp_path, resources, files, ano=2026, mes=8):
    with respx.mock() as router:
        mock_package(router, resources)
        for rid, body in files.items():
            router.get(url__regex=rf".*/resource/{rid}/download/.*").mock(
                return_value=httpx.Response(200, content=body)
            )
        rows = list(pe.ADAPTER.iter_rows(ano, mes, tmp_path))
    return rows


def test_une_ativos_e_inativos_normaliza_e_apaga_arquivos(tmp_path):
    resources = [res("ativos", 2026, 8, "a"), res("inativos", 2026, 8, "i")]
    rows = run(
        tmp_path,
        resources,
        {"a": csv_text(ATIVO_1, ATIVO_2).encode(), "i": csv_text(INATIVO_1).encode()},
    )
    assert [r["situacao"] for r in rows] == ["ATIVO", "ATIVO", "INATIVO"]
    r = rows[0]
    assert r["ident"] == r["matricula"] == "1020781/03" and r["nome"] == "FULANO DE TESTE"
    assert r["cpf_mascarado"] == "***.111.222-**" and r["orgao"] == "SEC TESTE"
    assert r["vinculo"] == "SERVIDOR EFETIVO" and r["cargo"] == "ANALISTA"
    assert r["valor_bruto"] == 16411.84 and r["valor_liquido"] == 12652.8
    assert r["valor_descontos"] == 3759.04
    assert r["detalhes"] == {
        "vencimento_cargo": 10230.71,
        "remuneracao": 11765.32,
        "outras_vantagens": 4646.52,
        "descontos_previdencia": 1432.3,
        "imposto_renda": 2326.74,
    }  # zeros ficam de fora
    assert rows[1]["cargo"] is None  # 'NA' vira vazio
    assert rows[2]["cargo"] is None and rows[2]["valor_liquido"] == 4700.0
    assert all(base.normalize_row(x) for x in rows)
    assert list(tmp_path.iterdir()) == []  # CSVs temporários apagados


def test_ident_estavel_sem_matricula_usa_md5_de_nome_e_orgao(tmp_path):
    resources = [res("ativos", 2026, 8, "a"), res("inativos", 2026, 8, "i")]
    files = {"a": csv_text(ATIVO_2).encode(), "i": csv_text(INATIVO_1).encode()}
    primeira = run(tmp_path, resources, files)[0]
    segunda = run(tmp_path, resources, files)[0]
    esperado = hashlib.md5(b"BELTRANA EXEMPLO|SEC TESTE").hexdigest()[:16]
    assert primeira["matricula"] is None
    assert primeira["ident"] == segunda["ident"] == esperado


def test_bom_latin1_linha_curta_e_decimais(tmp_path):
    latin = "SEC AÇÃO;***.1.2-**;9/1;JOÃO FICTÍCIO;EFETIVO;ENGENHEIRO;1.234,56;NA;0;0;0;0;0;2.000,50;0;0;0;0;;1.500,25;"
    curta = "SEC TESTE;***.9.9-**;5/1;MARIA CURTA;EFETIVO"  # faltam colunas
    corpo = ("\r\n".join([HEADER, latin, curta, "", ATIVO_1]) + "\r\n").encode("latin-1")
    bom = b"\xef\xbb\xbf" + csv_text(ATIVO_2).encode()
    resources = [res("ativos", 2026, 8, "a"), res("inativos", 2026, 8, "i")]
    rows = run(tmp_path, resources, {"a": corpo, "i": bom})
    nomes = [r["nome"] for r in rows]
    assert nomes == ["JOÃO FICTÍCIO", "MARIA CURTA", "FULANO DE TESTE", "BELTRANA EXEMPLO"]
    joao, maria = rows[0], rows[1]
    assert joao["orgao"] == "SEC AÇÃO" and joao["valor_bruto"] == 2000.5
    assert joao["detalhes"] == {"vencimento_cargo": 1234.56} and joao["valor_descontos"] == 500.25
    assert maria["valor_bruto"] == 0.0 and maria["valor_liquido"] == 0.0 and maria["detalhes"] == {}
    assert rows[3]["situacao"] == "INATIVO" and rows[3]["nome"] == "BELTRANA EXEMPLO"  # BOM tratado


def test_parse_decimal():
    f = pe.parse_decimal
    assert f("1893.21") == 1893.21 and f("1.234,56") == 1234.56 and f("1,234.56") == 1234.56
    assert f("12,5") == 12.5 and f("") == 0.0 and f(None) == 0.0 and f("NA") == 0.0
    assert f("abc") == 0.0 and f("R$ 10,00") == 10.0 and f("-5.5") == -5.5


def test_coluna_essencial_ausente_gera_erro_claro(tmp_path):
    header = HEADER.replace("r_valor_liquido", "r_liquido_x")
    resources = [res("ativos", 2026, 8, "a")]
    with pytest.raises(pe.PEFormatError, match="valor_liquido"):
        run(tmp_path, resources, {"a": csv_text(ATIVO_1, header=header).encode()})
    assert list(tmp_path.iterdir()) == []


def test_sem_inativos_segue_com_ativos_e_sem_ativos_falha(tmp_path):
    rows = run(tmp_path, [res("ativos", 2026, 8, "a")], {"a": csv_text(ATIVO_1).encode()})
    assert len(rows) == 1 and rows[0]["situacao"] == "ATIVO"
    with pytest.raises(RuntimeError, match="não publicado"):
        run(tmp_path, [res("inativos", 2026, 8, "i")], {})


def test_cabecalho_com_acento_caixa_diferente_e_sem_prefixo(tmp_path):
    header = "Instituição;CPF;Matrícula;Nome;Categoria;Cargo;Total_Vantagens;Valor_Liquido"
    linha = "ORG TESTE;***.0.0-**;42/1;FULANO TESTE;EFETIVO;AUDITOR;3000,00;2500,00"
    rows = run(
        tmp_path, [res("ativos", 2026, 8, "a")], {"a": csv_text(linha, header=header).encode()}
    )
    assert rows[0]["ident"] == "42/1" and rows[0]["valor_descontos"] == 500.0
    assert rows[0]["orgao"] == "ORG TESTE"


def test_download_com_falha_tenta_de_novo(tmp_path):
    resources = [res("ativos", 2026, 8, "a")]
    with respx.mock() as router:
        mock_package(router, resources)
        router.get(url__regex=r".*/resource/a/download/.*").mock(
            side_effect=[
                httpx.Response(503),
                httpx.Response(200, content=csv_text(ATIVO_1).encode()),
            ]
        )
        rows = list(pe.ADAPTER.iter_rows(2026, 8, tmp_path))
    assert len(rows) == 1


def test_adapter_registrado():
    from app.sources import ADAPTERS

    assert pe.ADAPTER.uf == "PE" and pe.ADAPTER.nome == "Governo de Pernambuco"
    assert ADAPTERS["PE"] is pe.ADAPTER

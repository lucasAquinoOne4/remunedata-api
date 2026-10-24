"""tests/test_source_ju.py — adaptador JU (DadosJusBR): parse, mapeamento de valores e segurança de host."""

import pytest

from app.sources import ADAPTERS
from app.sources import ju

CSV = (
    "id_contracheque;orgao;mes;ano;nome;matricula;funcao;local_trabalho;salario;beneficios;descontos;remuneracao;situacao\n"
    "1;tjdft;12;2024;ACACIA SOARES;;JUIZ SUBSTITUTO;8A VARA;35845,21;153917,84;55866,40;133896,65;\n"
    "2;tjdft;12;2024;;;X;;1,00;0,00;0,00;1,00;\n"
    "3;tjdft;12;2024;BRUNO LIMA;123;ANALISTA;;10.000,00;500,00;1.000,00;9.500,00;INATIVO\n"
)


def test_parse_mapeia_bruto_liquido_e_descarta_sem_nome():
    rows = list(ju.parse_contracheque(CSV, "tjdft", inconsistente=True))
    assert [r["nome"] for r in rows] == ["ACACIA SOARES", "BRUNO LIMA"]
    a, b = rows
    assert round(a["valor_bruto"], 2) == 189763.05 and a["valor_liquido"] == 133896.65
    assert a["orgao"] == "TJDFT" and a["matricula"] is None and a["situacao"] == "ATIVO"
    assert a["detalhes"]["lotacao"] == "8A VARA" and a["detalhes"]["fonte_inconsistente"] is True
    assert b["valor_bruto"] == 10500.0 and b["matricula"] == "123" and b["situacao"] == "INATIVO"
    assert a["ident"] != b["ident"] and len(a["ident"]) == 16


def test_host_fora_da_lista_e_recusado():
    with pytest.raises(ju.JUSourceError):
        ju._check_host("https://evil.example.com/x.zip")
    ju._check_host("https://dadosjusbr-public.s3.amazonaws.com/tjdft/datapackage/tjdft-2024-12.zip")


def test_orgaos_configurados_sao_validados(monkeypatch):
    class S:
        dadosjusbr_orgaos = "tjdft, MPDFT ,../etc,tredf"

    monkeypatch.setattr(ju, "get_settings", lambda: S())
    assert ju._orgaos() == ["tjdft", "mpdft", "tredf"]


def test_adaptador_registrado():
    assert ADAPTERS["JU"].uf == "JU"

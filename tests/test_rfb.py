"""
tests/test_rfb.py
Participação societária (CNPJ/RFB): carga atômica e idempotente, minimização (só 6 dígitos do CPF)
e regra de cruzamento estrita (nome idêntico E 6 dígitos centrais).
"""

import csv
import json
from pathlib import Path

import pytest
from sqlalchemy import text

from app.persistence.models import FederalRemuneracao
from app.services import people, rfb

S = rfb.SOCIOS_COLS
E = rfb.EMPRESAS_COLS


def _w(path: Path, header, rows):
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        w.writerows(rows)


def _make(d: Path, socios=None, empresas=None):
    d.mkdir(parents=True, exist_ok=True)
    _w(d / "socios.csv", S, socios if socios is not None else [
        ["11111111", "JOSÉ DA SILVA", "JOSE DA SILVA", "456789", "49", "2015-03-10", "5"],
        ["22222222", "JOSÉ DA SILVA", "JOSE DA SILVA", "456789", "22", "2020-01-05", "5"],
        ["33333333", "JOSE DA SILVA", "JOSE DA SILVA", "000000", "49", "2010-01-01", "4"],  # homônimo
        ["44444444", "JOSE DA SILVA", "JOSE DA SILVA", "12345", "49", "2010-01-01", "4"],  # cpf_core inválido
        ["11111111", "JOSÉ DA SILVA", "JOSE DA SILVA", "456789", "49", "2015-03-10", "5"],  # duplicata
    ])
    _w(d / "empresas.csv", E, empresas if empresas is not None else [
        ["11111111", "BAIXADA LTDA", "2062", "1000.50", "01", "08", "2019-01-01", "2015-03-01", "4711302", "DF", "9701", ""],
        ["22222222", "ATIVA SA", "2054", "50000", "05", "02", "2020-01-05", "2020-01-02", "6201501", "GO", "9702", "ATIVA TECH"],
        ["33333333", "HOMONIMA LTDA", "2062", "", "01", "02", "", "", "", "SP", "", ""],
    ])
    _w(d / "qualificacoes.csv", ["cod", "descricao"], [["49", "Sócio-Administrador"], ["22", "Sócio"]])
    _w(d / "naturezas.csv", ["cod", "descricao"], [["2062", "Sociedade Empresária Limitada"], ["2054", "Sociedade Anônima Fechada"]])
    _w(d / "cnaes.csv", ["cod", "descricao"], [["6201501", "Desenvolvimento de programas de computador"]])
    (d / "manifest.json").write_text(json.dumps({"mes": "2026-09", "contagens": {}, "hashes": {}}))
    return d


@pytest.fixture
def dir_ok(tmp_path):
    return _make(tmp_path / "ok")


async def _count(session, table):
    return (await session.execute(text(f"select count(*) from {table}"))).scalar()


@pytest.mark.asyncio
async def test_carga_idempotente_e_descarta_cpf_core_invalido(db_session, dir_ok):
    res = await rfb.load_dir(db_session, dir_ok)
    assert res["mes"] == "2026-09" and res["empresas"] == 3
    assert res["socios"] == 3 and res["socios_descartados"] == 1  # duplicata ignorada, cpf_core inválido descartado
    again = await rfb.load_dir(db_session, dir_ok)
    assert again == res
    assert await _count(db_session, "rfb_socio") == 3
    assert await _count(db_session, "rfb_carga") == 1
    assert (await db_session.execute(text("select count(*) from rfb_dominio where tipo='situacao'"))).scalar() == 5


@pytest.mark.asyncio
async def test_cabecalho_invalido_e_recusado_mantendo_dados(db_session, dir_ok, tmp_path):
    await rfb.load_dir(db_session, dir_ok)
    ruim = _make(tmp_path / "ruim")
    _w(ruim / "socios.csv", [c for c in S if c != "cpf_core"], [["1", "A", "A", "49", "", ""]])
    with pytest.raises(ValueError, match="cpf_core"):
        await rfb.load_dir(db_session, ruim)
    assert await _count(db_session, "rfb_socio") == 3


@pytest.mark.asyncio
async def test_diretorio_vazio_e_descartado_mantendo_dados_antigos(db_session, dir_ok, tmp_path):
    await rfb.load_dir(db_session, dir_ok)
    for socios, empresas in (([], None), (None, [])):
        vazio = _make(tmp_path / f"vazio{bool(socios)}{bool(empresas)}", socios=socios, empresas=empresas)
        with pytest.raises(ValueError, match="descartada"):
            await rfb.load_dir(db_session, vazio)
    assert await _count(db_session, "rfb_socio") == 3 and await _count(db_session, "rfb_empresa") == 3


@pytest.mark.asyncio
async def test_hash_do_manifest_divergente_recusa_a_carga(db_session, dir_ok):
    (dir_ok / "manifest.json").write_text(json.dumps({"mes": "2026-09", "hashes": {"socios.csv": "0" * 64}}))
    with pytest.raises(ValueError, match="hash"):
        await rfb.load_dir(db_session, dir_ok)
    assert await _count(db_session, "rfb_socio") == 0


@pytest.mark.asyncio
async def test_sem_campos_proibidos_no_banco(db_session, dir_ok):
    await rfb.load_dir(db_session, dir_ok)
    cols = (
        await db_session.execute(
            text("select column_name from information_schema.columns where table_name in ('rfb_socio','rfb_empresa')")
        )
    ).scalars().all()
    for proibido in ("cpf", "cpf_completo", "email", "telefone", "endereco", "logradouro", "cep"):
        assert proibido not in cols
    assert max(len(c) for c in (await db_session.execute(text("select cpf_core from rfb_socio"))).scalars()) == 6


@pytest.mark.asyncio
async def test_lookup_exige_nome_e_cpf_central(db_session, dir_ok):
    await rfb.load_dir(db_session, dir_ok)
    found = await rfb.lookup(db_session, "José da Silva", "456789")
    assert {f["cnpj_basico"] for f in found} == {"11111111", "22222222"}  # homônimo (outro CPF) não entra
    assert await rfb.lookup(db_session, "Jose da Silva", "999999") == []
    assert await rfb.lookup(db_session, "Jose da Silva", None) == []  # sem cpf não cruza
    assert await rfb.lookup(db_session, "Outro Nome", "456789") == []


@pytest.mark.asyncio
async def test_lookup_formato_e_ordenacao(db_session, dir_ok):
    await rfb.load_dir(db_session, dir_ok)
    found = await rfb.lookup(db_session, "JOSE DA SILVA", "456789")
    assert [f["cnpj_basico"] for f in found] == ["22222222", "11111111"]  # ativa antes da baixada
    ativa = found[0]
    assert ativa == {
        "cnpj_basico": "22222222", "razao_social": "ATIVA SA", "nome_fantasia": "ATIVA TECH",
        "situacao_cadastral": "Ativa", "data_situacao": "2020-01-05", "data_inicio_atividade": "2020-01-02",
        "cnae_principal": "6201501", "cnae_descricao": "Desenvolvimento de programas de computador",
        "uf": "GO", "natureza_juridica": "Sociedade Anônima Fechada", "porte": "Demais",
        "capital_social": 50000.0, "qualificacao": "Sócio", "data_entrada": "2020-01-05",
        "faixa_etaria": "5", "confianca": "provavel",
        "motivo": "mesmo nome e mesmos 6 dígitos centrais do CPF",
    }
    baixada = found[1]
    assert baixada["situacao_cadastral"] == "Baixada" and baixada["cnae_descricao"] is None
    assert baixada["qualificacao"] == "Sócio-Administrador" and baixada["nome_fantasia"] is None
    assert len(await rfb.lookup(db_session, "JOSE DA SILVA", "456789", limit=1)) == 1


@pytest.mark.asyncio
async def test_perfil_traz_participacoes_serializaveis(db_session, dir_ok):
    await rfb.load_dir(db_session, dir_ok)
    db_session.add_all([
        FederalRemuneracao(id_servidor_portal=1, categoria="CIVIL", ano=2026, mes=8, nome="JOSÉ DA SILVA",
                           cpf_mascarado="***.456.789-**", valor_bruto=1000.0, valor_liquido=900.0),
        FederalRemuneracao(id_servidor_portal=2, categoria="CIVIL", ano=2026, mes=8, nome="JOSÉ DA SILVA",
                           cpf_mascarado="***.999.999-**", valor_bruto=1000.0, valor_liquido=900.0),
        FederalRemuneracao(id_servidor_portal=3, categoria="CIVIL", ano=2026, mes=8, nome="JOSÉ DA SILVA",
                           cpf_mascarado=None, valor_bruto=1000.0, valor_liquido=900.0),
    ])
    await db_session.commit()

    perfil = await people.person(db_session, "FEDERAL", "1", None)
    assert [p["cnpj_basico"] for p in perfil["participacoes_societarias"]] == ["22222222", "11111111"]
    json.dumps(perfil["participacoes_societarias"])  # datas como texto: cabe no cache
    assert (await people.person(db_session, "FEDERAL", "2", None))["participacoes_societarias"] == []
    assert (await people.person(db_session, "FEDERAL", "3", None))["participacoes_societarias"] == []

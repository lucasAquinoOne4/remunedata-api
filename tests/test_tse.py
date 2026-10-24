"""
tests/test_tse.py
Enriquecimento TSE: LGPD (só 6 dígitos centrais do CPF), idempotência e regra de cruzamento estrita.
"""

import zipfile

import pytest
from sqlalchemy import text

from app.services import tse

CAND_HEADER = ["SQ_CANDIDATO", "NM_CANDIDATO", "NR_CPF_CANDIDATO", "DS_EMAIL", "SG_UF", "DS_CARGO",
               "SG_PARTIDO", "DS_OCUPACAO", "DS_SITUACAO_CANDIDATURA", "DS_SIT_TOT_TURNO"]
BEM_HEADER = ["SQ_CANDIDATO", "DS_TIPO_BEM_CANDIDATO", "VR_BEM_CANDIDATO"]


def _csv(header, rows):
    lines = [";".join(f'"{h}"' for h in header)]
    lines += [";".join(f'"{c}"' for c in r) for r in rows]
    return "\n".join(lines).encode("latin-1")


@pytest.fixture
def zips(tmp_path):
    cand = tmp_path / "consulta_cand_2022.zip"
    with zipfile.ZipFile(cand, "w") as z:
        z.writestr("consulta_cand_2022_BRASIL.csv", _csv(CAND_HEADER, [
            ["1", "JOSÉ DA SILVA", "12345678909", "fulano@x.com", "DF", "Deputado", "ABC", "Servidor", "Deferido", "Eleito"],
            ["2", "MARIA SOUZA", "98765432100", "NÃO DIVULGÁVEL", "GO", "Senador", "XYZ", "#NULO", "Deferido", "Não eleito"],
            ["1", "JOSÉ DA SILVA", "12345678909", "dup@x.com", "DF", "Deputado", "ABC", "Servidor", "Deferido", "Eleito"],
        ]))
        z.writestr("consulta_cand_2022_BR.csv", _csv(CAND_HEADER, [["9", "NAO USAR", "11111111111", "", "BR", "", "", "", "", ""]]))
    bem = tmp_path / "bem_candidato_2022.zip"
    with zipfile.ZipFile(bem, "w") as z:
        z.writestr("bem_candidato_2022_BRASIL.csv", _csv(BEM_HEADER, [
            ["1", "Casa", "300000,50"], ["1", "Casa", "100.000,00"], ["1", "Veículo", "50000,00"],
        ]))
    return cand, bem


def test_cpf_core_pega_digitos_4_a_9():
    assert tse.cpf_core_from_full("123.456.789-09") == "456789"
    assert tse.cpf_core_from_full("123") is None


@pytest.mark.asyncio
async def test_carga_nao_grava_cpf_completo_nem_email_e_agrega_bens(db_session, zips):
    cand, bem = zips
    res = await tse.load_year(db_session, 2022, cand, bem)
    assert res == {"ano": 2022, "candidaturas": 2, "bens": 2}  # duplicata e arquivo _BR ignorados

    rows = (await db_session.execute(text("select * from tse_candidato order by sq_candidato"))).mappings().all()
    blob = " ".join(str(v) for r in rows for v in r.values())
    assert "12345678909" not in blob and "fulano@x.com" not in blob and "98765432100" not in blob
    assert rows[0]["cpf_core"] == "456789" and rows[1]["ocupacao"] is None

    again = await tse.load_year(db_session, 2022, cand, bem)
    assert again == res
    assert (await db_session.execute(text("select count(*) from tse_candidato"))).scalar() == 2


@pytest.mark.asyncio
async def test_lookup_exige_nome_e_cpf_central(db_session, zips):
    await tse.load_year(db_session, 2022, *zips)

    found = await tse.lookup(db_session, "Jose da Silva", "456789")
    assert len(found) == 1 and found[0]["patrimonio_declarado"] == 450000.5
    assert {b["tipo"]: b["quantidade"] for b in found[0]["bens"]} == {"Casa": 2, "Veículo": 1}
    assert await tse.lookup(db_session, "Jose da Silva", "000000") == []  # homônimo com outro CPF
    assert await tse.lookup(db_session, "Jose da Silva", None) == []  # sem CPF não cruza
    assert await tse.lookup(db_session, "Outro Nome", "456789") == []


@pytest.mark.asyncio
async def test_carga_sem_candidaturas_e_descartada_e_mantem_o_ano(db_session, zips, tmp_path):
    await tse.load_year(db_session, 2022, *zips)
    vazio = tmp_path / "vazio.zip"
    with zipfile.ZipFile(vazio, "w") as z:
        z.writestr("consulta_cand_2022_BRASIL.csv", _csv(CAND_HEADER, []))
    with pytest.raises(ValueError):
        await tse.load_year(db_session, 2022, vazio, zips[1])
    assert (await db_session.execute(text("select count(*) from tse_candidato"))).scalar() == 2

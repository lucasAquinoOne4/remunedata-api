"""
tests/test_economia.py
Contexto econômico: parsing das séries (BCB/IBGE), deflação, salários mínimos, teto e o perfil.
"""

from datetime import date

import httpx
import pytest
import respx
from sqlalchemy import delete

from app.persistence.models import IndiceEconomico
from app.persistence.repositories import FederalRepository
from app.services import economia
from app.services.economia import Contexto, _parse_bcb, _parse_sidra


def test_parse_bcb_e_sidra_ignoram_linhas_invalidas():
    bcb = _parse_bcb(
        [{"data": "01/03/2026", "valor": "1621.00"}, {"data": "xx", "valor": "1"}, {"valor": "2"}]
    )
    assert bcb == [(2026, 3, 1621.0)]
    sidra = _parse_sidra(
        [{"D3C": "Mês (Código)"}, {"D3C": "202607", "V": "7657.73"}, {"D3C": "x", "V": "y"}]
    )
    assert sidra == [(2026, 7, 7657.73)]


def test_contexto_deflaciona_para_o_mes_de_referencia_e_calcula_sm_e_teto():
    ctx = Contexto(
        ipca={(2025, 1): 100.0, (2026, 1): 110.0},
        sm={(2025, 1): 1000.0},
        tetos=[(date(2023, 4, 1), 41650.92), (date(2025, 2, 1), 46366.19)],
    )
    assert ctx.referencia == (2026, 1)
    e = ctx.enriquecer(2025, 1, 5000.0)
    assert e["bruto_real"] == 5500.0  # 5000 * 110/100: reais de jan/2026
    assert e["salarios_minimos"] == 5.0
    assert e["teto"] == 41650.92 and e["pct_teto"] == round(100 * 5000 / 41650.92, 1)
    # após fev/2025 vale a 3ª parcela; sem índice/salário no mês, campos ficam nulos (não quebram)
    e2 = ctx.enriquecer(2026, 1, 46366.19)
    assert e2["teto"] == 46366.19 and e2["pct_teto"] == 100.0 and e2["salarios_minimos"] is None
    assert ctx.enriquecer(2010, 1, 100.0)["teto"] is None  # antes de qualquer vigência cadastrada


@pytest.mark.asyncio
@respx.mock
async def test_fetch_series_descarta_meses_futuros_do_salario_minimo():
    respx.get(url__regex=r".*sgs\.1619.*").mock(
        return_value=httpx.Response(
            200,
            json=[
                {"data": "01/08/2026", "valor": "1621.00"},
                {"data": "01/10/2026", "valor": "1621.00"},  # futuro: não realizado
            ],
        )
    )
    respx.get(url__regex=r".*sgs\.4390.*").mock(
        return_value=httpx.Response(200, json=[{"data": "01/08/2026", "valor": "1.09"}])
    )
    respx.get(url__regex=r".*apisidra.*").mock(
        return_value=httpx.Response(200, json=[{"D3C": "Mês"}, {"D3C": "202607", "V": "7657.73"}])
    )
    series = await economia.fetch_series(today=date(2026, 9, 15))
    assert series["salario_minimo"] == [(2026, 8, 1621.0)]
    assert series["ipca_indice"] == [(2026, 7, 7657.73)]


@pytest.mark.usefixtures("override_auth")
@pytest.mark.asyncio
async def test_perfil_traz_contexto_por_competencia(client, db_session, override_get_session):
    await db_session.execute(delete(IndiceEconomico).where(IndiceEconomico.ano >= 2070))
    await economia.store_series(
        db_session,
        {
            "ipca_indice": [(2077, 1, 100.0), (2077, 2, 120.0)],
            "salario_minimo": [(2077, 1, 2000.0)],
        },
    )
    try:
        await FederalRepository(db_session).replace_month(
            2077,
            1,
            iter(
                [
                    {
                        "id_servidor_portal": 9100,
                        "ano": 2077,
                        "mes": 1,
                        "nome": "CONTEXTO TESTE",
                        "cargo": "X",
                        "valor_bruto": 10000.0,
                    }
                ]
            ),
        )
        body = (await client.get("/api/v1/remuneration/person?fonte=FEDERAL&id=9100")).json()
        ctx = body["historico"][0]["contexto"]
        assert ctx["bruto_real"] == 12000.0  # reais de fev/2077 (mês de referência)
        assert ctx["salarios_minimos"] == 5.0
        assert body["contexto_base"]["referencia"] == "2077-02"
        assert "indenizatórias" in body["contexto_base"]["teto"]
    finally:
        await db_session.execute(delete(IndiceEconomico).where(IndiceEconomico.ano >= 2070))
        await db_session.commit()


def test_tarefa_economia_registrada_no_beat():
    from app.core.celery_app import celery_app

    assert "refresh-economia-monthly" in celery_app.conf.beat_schedule
    assert (
        celery_app.conf.beat_schedule["refresh-economia-monthly"]["task"] == "refresh_economia_task"
    )

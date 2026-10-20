"""
tests/test_senado.py
Testes da integração com a API de dados abertos do Senado Federal.
"""

from unittest.mock import AsyncMock

import pytest

from app.collector.senado import (
    SenadoCollector,
    build_record,
    index_cadastro,
    normalize_name,
    parse_brl,
)


def test_parse_brl_formatos():
    assert parse_brl("5.612,35") == 5612.35
    assert parse_brl("0,00") == 0.0
    assert parse_brl(None) == 0.0
    assert parse_brl("") == 0.0
    assert parse_brl("abc") == 0.0
    assert parse_brl(12.5) == 12.5


def _item(**kw):
    base = {
        "sequencial": 10,
        "nome": "ALINE DE AQUINO TESTE",
        "mes": 8,
        "ano": 2026,
        "tipo_folha": "Normal",
        "remuneracao_basica": "10.000,00",
        "vantagens_pessoais": "500,00",
        "diarias": "100,50",
        "previdencia": "1.100,00",
        "imposto_renda": "2.000,00",
        "remuneracao_liquida": "7.500,00",
    }
    base.update(kw)
    return base


def test_build_record_bruto_soma_proventos_e_ignora_deducoes():
    cadastro = {
        "sequencial": 10,
        "vinculo": "EFETIVO",
        "situacao": "ATIVO",
        "cargo": "ANALISTA",
        "lotacao": {"sigla": "SGM", "nome": "Secretaria-Geral da Mesa"},
    }
    rec = build_record(_item(), cadastro)
    assert rec.valor_bruto == 10600.50  # 10000 + 500 + 100,50 (sem previdência/IR)
    assert rec.valor_liquido == 7500.0
    assert rec.cargo == "ANALISTA"
    assert rec.lotacao_sigla == "SGM"
    assert rec.mes == 8 and rec.ano == 2026


def test_build_record_sem_cadastro():
    rec = build_record(_item(), None)
    assert rec.cargo is None
    assert rec.lotacao_nome is None
    assert rec.nome == "ALINE DE AQUINO TESTE"


@pytest.mark.asyncio
async def test_collector_mes_vazio_nao_grava():
    client = AsyncMock()
    client.get_remuneracoes.return_value = []
    repo = AsyncMock()
    result = await SenadoCollector(client, repo).collect_month(2026, 9)
    assert result["status"] == "empty"
    repo.replace_month.assert_not_called()
    client.get_servidores.assert_not_called()


@pytest.mark.asyncio
async def test_collector_remove_duplicatas_e_une_cadastro():
    client = AsyncMock()
    client.get_remuneracoes.return_value = [
        _item(sequencial=1),
        _item(sequencial=1),  # duplicata exata (mesma chave)
        _item(sequencial=1, tipo_folha="Suplementar"),
        _item(sequencial=2, nome="OUTRA PESSOA"),
    ]
    # cadastro precisa ser "completo" (>= MIN_CADASTRO): completa com registros de enchimento
    enchimento = [{"sequencial": 5000 + i, "nome": f"ENCHIMENTO {i}"} for i in range(1000)]
    client.get_servidores.return_value = enchimento + [
        {"sequencial": 999, "nome": "aline de aquino teste ", "cargo": "TECNICO"},
        {"sequencial": 998, "nome": "OUTRA PESSOA", "cargo": "A"},
        {"sequencial": 997, "nome": "OUTRA PESSOA", "cargo": "B"},  # homônimo: não junta
    ]
    repo = AsyncMock()

    result = await SenadoCollector(client, repo).collect_month(2026, 8)

    assert result == {"ano": 2026, "mes": 8, "registros": 3, "status": "success"}
    ano, mes, records = repo.replace_month.call_args.args
    assert (ano, mes) == (2026, 8)
    assert {(r.sequencial, r.tipo_folha) for r in records} == {
        (1, "Normal"),
        (1, "Suplementar"),
        (2, "Normal"),
    }
    assert next(r for r in records if r.sequencial == 1).cargo == "TECNICO"
    assert next(r for r in records if r.sequencial == 2).cargo is None  # homônimo


def test_sync_recent_months_virada_de_ano(monkeypatch):
    from datetime import datetime

    from app.workers import tasks

    class FakeDT(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 2, 10)

    monkeypatch.setattr("datetime.datetime", FakeDT)
    assert tasks._recent_months(3) == [(2026, 2), (2026, 1), (2025, 12)]


def test_build_record_cargo_e_funcao_como_objeto():
    rec = build_record(
        _item(),
        {"cargo": {"nome": "ANALISTA LEGISLATIVO"}, "funcao": {"codigo": 1, "nome": "CHEFE"}},
    )
    assert rec.cargo == "ANALISTA LEGISLATIVO"
    assert rec.funcao == "CHEFE"


def test_normalize_name_acento_e_espacos():
    assert normalize_name("  Luiz   Antônio de Paiva ") == "LUIZ ANTONIO DE PAIVA"


def test_index_cadastro_descarta_homonimos_e_nao_usa_sequencial():
    idx = index_cadastro(
        [
            {"sequencial": 1, "nome": "ANA", "cargo": "X"},
            {"sequencial": 2, "nome": "JOSE", "cargo": "Y"},
            {"sequencial": 3, "nome": "José", "cargo": "Z"},
        ]
    )
    assert set(idx) == {"ANA"}


# --- API (usa o banco de teste e os fixtures de auth dos demais testes de API) ---


@pytest.mark.usefixtures("override_auth")
@pytest.mark.asyncio
async def test_api_search_fonte_senado(db_session, override_get_session, override_auth, client):
    from app.persistence.repositories import SenadoRepository

    repo = SenadoRepository(db_session)
    await repo.replace_month(
        2098,
        7,
        [
            build_record(_item(sequencial=424242, nome="TESTE SENADO API", ano=2098, mes=7), None),
        ],
    )

    resp = await client.get("/api/v1/remuneration/?fonte=SENADO&nome=TESTE%20SENADO%20API&ano=2098")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    item = body["items"][0]
    assert item["fonte"] == "SENADO"
    assert item["mes_referencia"] == "07"
    assert item["valor_bruto"] == 10600.5

    # A fonte padrão (DF) não enxerga registros do Senado
    resp_df = await client.get("/api/v1/remuneration/?nome=TESTE%20SENADO%20API&ano=2098")
    assert resp_df.json()["total"] == 0

    # Fonte inválida é rejeitada
    assert (await client.get("/api/v1/remuneration/?fonte=XX")).status_code == 422

    # Reexecutar o mês é idempotente (substitui, não duplica)
    await repo.replace_month(
        2098,
        7,
        [build_record(_item(sequencial=424242, nome="TESTE SENADO API", ano=2098, mes=7), None)],
    )
    resp2 = await client.get(
        "/api/v1/remuneration/?fonte=SENADO&nome=TESTE%20SENADO%20API&ano=2098"
    )
    assert resp2.json()["total"] == 1

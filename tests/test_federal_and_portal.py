"""
tests/test_federal_and_portal.py
Fonte federal (ZIP do portal), endpoints do portal (períodos, sugestões, estatísticas, perfil,
contagem limitada) e restrição de carga a administradores.
"""

import uuid
import zipfile
from unittest.mock import AsyncMock, patch

import pytest

from app.api.deps import get_current_user
from app.collector.federal import FederalCollector, iter_records
from app.core.cache import cache
from app.main import app
from app.persistence.models import RemunerationCollected
from app.persistence.repositories import (
    ExecutionRepository,
    FederalRepository,
    RemunerationRepository,
    SenadoRepository,
)

CAD_HEADER = (
    "Id_SERVIDOR_PORTAL;NOME;CPF;MATRICULA;DESCRICAO_CARGO;CLASSE_CARGO;FUNCAO;ORG_LOTACAO;"
    "ORG_EXERCICIO;ORGSUP_EXERCICIO;TIPO_VINCULO;SITUACAO_VINCULO;REGIME_JURIDICO;"
    "JORNADA_DE_TRABALHO;UF_EXERCICIO"
).split(";")
REM_HEADER = (
    "ANO;MES;Id_SERVIDOR_PORTAL;CPF;NOME;REMUNERAÇÃO BÁSICA BRUTA (R$);ABATE-TETO (R$);"
    "GRATIFICAÇÃO NATALINA (R$);ABATE-TETO DA GRATIFICAÇÃO NATALINA (R$);FÉRIAS (R$);"
    "OUTRAS REMUNERAÇÕES EVENTUAIS (R$);IRRF (R$);PSS/RPGS (R$);DEMAIS DEDUÇÕES (R$);"
    "REMUNERAÇÃO APÓS DEDUÇÕES OBRIGATÓRIAS (R$);TOTAL DE VERBAS INDENIZATÓRIAS (R$)(*)"
).split(";")


def _csv(header, rows) -> bytes:
    lines = [";".join(f'"{c}"' for c in header)]
    lines += [";".join(f'"{c}"' for c in row) for row in rows]
    return "\n".join(lines).encode("latin-1")


def make_zip(path, ano=2026, mes=8):
    ym = f"{ano}{mes:02d}"
    cad = [
        # desordenado e com id repetido (o primeiro vence)
        [
            "20",
            "JOSÉ DA SILVA",
            "***.927.115-**",
            "111****",
            "ANALISTA",
            "A",
            "-1",
            "Lot X",
            "Órgão Y",
            "Min Z",
            "Cargo",
            "ATIVO",
            "RJU",
            "40 H",
            "DF",
        ],
        [
            "10",
            "MARIA SOUZA",
            "***.411.707-**",
            "222****",
            "MÉDICO",
            "B",
            "-1",
            "Lot A",
            "Órgão B",
            "Min C",
            "Cargo",
            "ATIVO",
            "CLT",
            "20 H",
            "SP",
        ],
        [
            "20",
            "OUTRO CADASTRO",
            "***.000.000-**",
            "999****",
            "DEVERIA SER IGNORADO",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
        ],
    ]
    rem = [
        [
            str(ano),
            f"{mes:02d}",
            "10",
            "***.411.707-**",
            "MARIA SOUZA",
            "10000,00",
            "-500,00",
            "0,00",
            "0,00",
            "800,00",
            "200,00",
            "-1500,00",
            "-900,00",
            "0,00",
            "8100,00",
            "300,00",
        ],
        [
            str(ano),
            f"{mes:02d}",
            "20",
            "***.927.115-**",
            "JOSÉ DA SILVA",
            "5.000,00",
            "0,00",
            "0,00",
            "0,00",
            "0,00",
            "0,00",
            "-300,00",
            "-400,00",
            "0,00",
            "4300,00",
            "0,00",
        ],
        [
            str(ano),
            f"{mes:02d}",
            "30",
            "***.123.456-**",
            "SEM CADASTRO",
            "1000,00",
            "0,00",
            "0,00",
            "0,00",
            "0,00",
            "0,00",
            "0,00",
            "0,00",
            "0,00",
            "1000,00",
            "0,00",
        ],
    ]
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"{ym}_Cadastro.csv", _csv(CAD_HEADER, cad))
        zf.writestr(f"{ym}_Remuneracao.csv", _csv(REM_HEADER, rem))
        zf.writestr(f"{ym}_Afastamentos.csv", b"x")


@pytest.fixture(autouse=True)
async def _limpa_cache():
    await cache.clear_prefix("remuneration:")
    yield
    await cache.clear_prefix("remuneration:")


def test_federal_join_por_id_primeiro_cadastro_vence_e_sem_cadastro(tmp_path):
    zip_path = tmp_path / "s.zip"
    make_zip(zip_path)
    rows = {r["id_servidor_portal"]: r for r in iter_records(zip_path, 2026, 8, tmp_path)}
    assert set(rows) == {10, 20, 30}
    assert rows[20]["cargo"] == "ANALISTA"  # primeiro cadastro, não o repetido
    assert rows[20]["org_exercicio"] == "Órgão Y" and rows[20]["uf_exercicio"] == "DF"
    assert rows[30]["cargo"] is None  # sem cadastro: grava assim mesmo
    maria = rows[10]
    # bruto = básica + abate-teto + natalina + abate natalina + férias + eventuais
    assert maria["valor_bruto"] == 10000 - 500 + 800 + 200
    assert maria["valor_liquido"] == 8100.0 and maria["irrf"] == -1500.0
    assert rows[20]["remuneracao_basica_bruta"] == 5000.0  # formato BR com milhar


def test_federal_layout_diferente_falha_com_mensagem(tmp_path):
    zip_path = tmp_path / "s.zip"
    ym = "202608"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr(f"{ym}_Cadastro.csv", _csv(["Id_SERVIDOR_PORTAL", "NOME"], [["1", "A"]]))
        zf.writestr(f"{ym}_Remuneracao.csv", _csv(["Id_SERVIDOR_PORTAL"], [["1"]]))
    with pytest.raises(RuntimeError, match="layout mudou"):
        list(iter_records(zip_path, 2026, 8, tmp_path))


@pytest.mark.asyncio
async def test_collector_federal_pula_mes_carregado_sem_requisicao_e_mes_nao_publicado(
    db_session, tmp_path
):
    repo = FederalRepository(db_session)
    client = AsyncMock()
    client.is_published.return_value = False
    collector = FederalCollector(client, repo)

    assert (await collector.collect_month(2091, 5))["status"] == "empty"  # não publicado
    client.download.assert_not_called()

    # mês já carregado: nenhuma chamada ao portal (nem HEAD)
    await repo.replace_month(
        2091, 6, iter([{"id_servidor_portal": 1, "ano": 2091, "mes": 6, "nome": "X"}])
    )
    client.reset_mock()
    result = await collector.collect_month(2091, 6)
    assert result["status"] == "already_loaded"
    client.is_published.assert_not_called()
    client.download.assert_not_called()


@pytest.mark.asyncio
async def test_collector_federal_carrega_zip_e_e_idempotente(db_session, tmp_path):
    zip_path = tmp_path / "src.zip"
    make_zip(zip_path, ano=2090, mes=3)

    async def fake_download(ano, mes, dest):
        dest.write_bytes(zip_path.read_bytes())
        return 1

    client = AsyncMock()
    client.is_published.return_value = True
    client.download.side_effect = fake_download
    collector = FederalCollector(client, FederalRepository(db_session))

    first = await collector.collect_month(2090, 3)
    assert first == {"ano": 2090, "mes": 3, "registros": 3, "status": "success"}
    # força: substitui sem duplicar
    again = await collector.collect_month(2090, 3, force=True)
    assert again["registros"] == 3
    items, total = await FederalRepository(db_session).search(ano=2090, mes="3", limit=10, offset=0)
    assert total == 3 and {i["fonte"] for i in items} == {"FEDERAL"}


# ───────────── endpoints ─────────────


async def _seed_df(db_session, ident, nome, cpf, ano, mes, bruto, cargo="ANALISTA"):
    exec_repo = ExecutionRepository(db_session)
    annual = await exec_repo.get_or_create_annual(ano)
    monthly = await exec_repo.create_monthly(annual.id, f"{mes:02d}")
    item = RemunerationCollected(
        execution_id=annual.id,
        monthly_execution_id=monthly.id,
        ano_exercicio=ano,
        mes_referencia=f"{mes:02d}",
        codigo_identificacao=ident,
        codigo_matricula="M1",
        cpf_servidor=cpf,
        nome_servidor=nome,
        cargo=cargo,
        nome_orgao="SECRETARIA TESTE",
        valor_bruto=bruto,
        valor_liquido=bruto * 0.8,
        raw_payload_json=f'{{"u": "{uuid.uuid4()}"}}',
    )
    await RemunerationRepository(db_session).save_batch([item])


@pytest.mark.usefixtures("override_auth")
@pytest.mark.asyncio
async def test_busca_federal_periods_suggest_stats(client, db_session, override_get_session):
    f_repo = FederalRepository(db_session)
    rows = [
        {
            "id_servidor_portal": 500 + i,
            "ano": 2089,
            "mes": 4,
            "nome": f"FULANO TESTE {i}",
            "cargo": "AUDITOR",
            "org_exercicio": "MIN X",
            "valor_bruto": 10000.0 + i * 1000,
            "valor_liquido": 8000.0,
        }
        for i in range(5)
    ]
    await f_repo.replace_month(2089, 4, iter(rows))

    r = await client.get("/api/v1/remuneration/?fonte=FEDERAL&nome=fulano%20teste&ano=2089")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 5 and body["total_capped"] is False
    assert body["items"][0]["fonte"] == "FEDERAL"

    periods = (await client.get("/api/v1/remuneration/periods")).json()
    assert {"DF", "SENADO", "FEDERAL"} <= set(periods)
    assert {"ano": 2089, "mes": "04"} in periods["FEDERAL"]

    sug = (await client.get("/api/v1/remuneration/suggest?q=fulano&fonte=FEDERAL")).json()[
        "suggestions"
    ]
    assert sug and all(s["fonte"] == "FEDERAL" for s in sug)
    assert (await client.get("/api/v1/remuneration/suggest?q=fu")).status_code == 422  # mínimo 3

    st = (await client.get("/api/v1/remuneration/stats?fonte=FEDERAL&ano=2089")).json()
    assert st["total_servidores"] == 5 and st["mensal"][0]["registros"] == 5
    assert st["top_orgaos"][0]["nome"] == "MIN X"
    assert sum(f["registros"] for f in st["faixas"]) == 5


@pytest.mark.asyncio
async def test_contagem_e_limitada(db_session):
    from app.persistence import repositories

    repo = FederalRepository(db_session)
    rows = [
        {"id_servidor_portal": 9000 + i, "ano": 2088, "mes": 1, "nome": "NOME LIMITE"}
        for i in range(12)
    ]
    await repo.replace_month(2088, 1, iter(rows))
    with patch.object(repositories, "COUNT_CAP", 5):
        _, total = await repo.search(nome="nome limite", ano=2088, limit=3, offset=0)
    assert total == 6  # COUNT_CAP + 1 sinaliza "mais que o teto"


@pytest.mark.usefixtures("override_auth")
@pytest.mark.asyncio
async def test_perfil_cruza_fontes_provavel_possivel_e_descarta_cpf_diferente(
    client, db_session, override_get_session
):
    nome = "ZULEIDE CRUZADA TESTE"
    await _seed_df(db_session, "DFID1", nome, "***927115**", 2087, 6, 9000.0)
    await _seed_df(
        db_session, "DFID2", nome, "***111222**", 2087, 6, 7000.0
    )  # CPF central diferente
    f_repo = FederalRepository(db_session)
    await f_repo.replace_month(
        2087,
        6,
        iter(
            [
                {
                    "id_servidor_portal": 777001,
                    "ano": 2087,
                    "mes": 6,
                    "nome": nome,
                    "cpf_mascarado": "***.927.115-**",
                    "cargo": "ANALISTA",
                    "org_exercicio": "MIN F",
                    "valor_bruto": 12000.0,
                    "valor_liquido": 9000.0,
                    "remuneracao_basica_bruta": 12000.0,
                    "irrf": -2000.0,
                }
            ]
        ),
    )
    s_repo = SenadoRepository(db_session)
    from app.collector.senado import build_record

    await s_repo.replace_month(
        2087,
        6,
        [
            build_record(
                {
                    "sequencial": 3001,
                    "nome": nome,
                    "tipo_folha": "Normal",
                    "remuneracao_basica": "20.000,00",
                },
                None,
                2087,
                6,
            )
        ],
    )

    r = await client.get("/api/v1/remuneration/person?fonte=FEDERAL&id=777001")
    assert r.status_code == 200
    body = r.json()
    assert body["identidade"]["nome"] == nome and body["ultimo"]["valor_bruto"] == 12000.0
    assert body["historico"][0]["componentes"]["irrf"] == -2000.0
    cruz = {(o["fonte"], o["id"]): o["confianca"] for o in body["outras_fontes"]}
    assert cruz[("DF", "DFID1")] == "provavel"  # nome + 6 dígitos centrais do CPF
    assert ("DF", "DFID2") not in cruz  # CPF central diferente: pessoa diferente
    assert cruz[("SENADO", "3001")] == "possivel"  # Senado não tem CPF: só nome

    df = await client.get("/api/v1/remuneration/person?fonte=DF&id=DFID1&matricula=M1")
    assert df.status_code == 200 and df.json()["identidade"]["uf"] == "DF"
    assert (
        await client.get("/api/v1/remuneration/person?fonte=DF&id=NAOEXISTE")
    ).status_code == 404


@pytest.mark.asyncio
async def test_ficha_federal_so_com_chave_e_e_cacheada(client, db_session, override_get_session):
    app.dependency_overrides[get_current_user] = lambda: {"sub": "u", "email": "test@example.com"}
    from app.api.deps import require_active_subscription

    app.dependency_overrides[require_active_subscription] = lambda: {
        "sub": "u",
        "email": "test@example.com",
    }
    try:
        await FederalRepository(db_session).replace_month(
            2086,
            2,
            iter(
                [
                    {
                        "id_servidor_portal": 888001,
                        "ano": 2086,
                        "mes": 2,
                        "nome": "FICHA TESTE",
                        "cargo": "X",
                    }
                ]
            ),
        )
        ficha = {"fichasCargoEfetivo": [{"cargo": "ASM"}]}
        with patch(
            "app.services.people.federal_ficha", new=AsyncMock(return_value=ficha)
        ) as mocked:
            a = (await client.get("/api/v1/remuneration/person?fonte=FEDERAL&id=888001")).json()
            b = (await client.get("/api/v1/remuneration/person?fonte=FEDERAL&id=888001")).json()
        assert a["ficha_federal"] == ficha and b["ficha_federal"] == ficha
        assert mocked.call_count == 1  # segunda vez vem do cache (uma chamada por pessoa)
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(require_active_subscription, None)


# ───────────── carga só para admin ─────────────


@pytest.mark.asyncio
async def test_execucoes_e_cargas_exigem_admin(client, override_get_session):
    app.dependency_overrides[get_current_user] = lambda: {"sub": "u", "email": "comum@example.com"}
    try:
        for method, url in (
            ("GET", "/api/v1/executions/"),
            ("GET", "/api/v1/executions/1"),
            ("POST", "/api/v1/executions/?ano=2025"),
        ):
            resp = await client.request(method, url)
            assert resp.status_code == 403, url
        from app.core.config import settings

        admin = settings.admin_path_prefix
        assert (await client.get(f"/api/v1/{admin}/loads")).status_code == 403
        assert (
            await client.post(f"/api/v1/{admin}/loads/FEDERAL?ano=2026&mes=8")
        ).status_code == 403
    finally:
        app.dependency_overrides.pop(get_current_user, None)


@pytest.mark.asyncio
async def test_admin_status_e_disparo_de_carga(client, override_get_session):
    from app.core.config import settings

    app.dependency_overrides[get_current_user] = lambda: {"sub": "a", "email": "test@example.com"}
    admin = settings.admin_path_prefix
    try:
        status = (await client.get(f"/api/v1/{admin}/loads")).json()
        assert {f["fonte"] for f in status["fontes"]} == {"DF", "SENADO", "FEDERAL"}
        assert all("em_execucao" in f for f in status["fontes"])

        with patch("app.api.endpoints.admin.collect_federal_month_task") as task:
            task.delay.return_value.id = "task-123"
            ok = await client.post(f"/api/v1/{admin}/loads/FEDERAL?ano=2026&mes=8")
        assert ok.json() == {"task_id": "task-123", "status": "queued"}
        assert (await client.post(f"/api/v1/{admin}/loads/FEDERAL?ano=2026")).status_code == 422
        assert (await client.post(f"/api/v1/{admin}/loads/XX?ano=2026&mes=1")).status_code == 404
    finally:
        app.dependency_overrides.pop(get_current_user, None)


@pytest.mark.usefixtures("override_auth")
@pytest.mark.asyncio
async def test_export_busca_csv_xlsx_e_exige_filtro(client, db_session, override_get_session):
    await FederalRepository(db_session).replace_month(
        2085,
        7,
        iter(
            [
                {
                    "id_servidor_portal": 4000 + i,
                    "ano": 2085,
                    "mes": 7,
                    "nome": f"EXPORTA {i}",
                    "cargo": "AUDITOR",
                    "org_exercicio": "MIN E",
                    "valor_bruto": 1000.5 + i,
                    "valor_liquido": 900.0,
                }
                for i in range(3)
            ]
        ),
    )
    csv = await client.get("/api/v1/remuneration/export?fonte=FEDERAL&nome=exporta&ano=2085")
    assert csv.status_code == 200 and "text/csv" in csv.headers["content-type"]
    text = csv.content.decode("utf-8-sig")
    assert text.splitlines()[0].startswith("Fonte;Ano;Mês;Nome") and text.count("EXPORTA") == 3
    assert "1000,5" in text  # vírgula decimal (pt-BR)
    xlsx = await client.get("/api/v1/remuneration/export?formato=xlsx&fonte=FEDERAL&nome=exporta")
    assert xlsx.status_code == 200 and xlsx.content[:2] == b"PK"  # arquivo zip/xlsx
    assert (await client.get("/api/v1/remuneration/export?fonte=FEDERAL")).status_code == 422


@pytest.mark.asyncio
async def test_export_de_execucao_continua_sendo_de_assinante(
    client, db_session, override_get_session
):
    """Só disparar/listar execuções é admin; o export do plano pago não pode virar admin."""
    from app.api.deps import require_active_subscription

    app.dependency_overrides[get_current_user] = lambda: {"sub": "u", "email": "comum@example.com"}
    app.dependency_overrides[require_active_subscription] = lambda: {
        "sub": "u",
        "email": "comum@example.com",
    }
    try:
        exec_repo = ExecutionRepository(db_session)
        annual = await exec_repo.get_or_create_annual(2084)
        resp = await client.get(f"/api/v1/executions/{annual.id}/export")
        assert resp.status_code == 404  # chegou na regra de negócio (sem dados), não em 403
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(require_active_subscription, None)

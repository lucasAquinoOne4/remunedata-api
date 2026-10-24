"""Testes puros (sem banco, sem rede) do ETL da Receita Federal: scripts/rfb_etl.py.

Rodar SOMENTE assim (não toca no banco):
    python -m pytest --noconftest -p no:cacheprovider tests/test_rfb_etl.py
"""
import csv
import importlib.util
import json
import sys
import os
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("rfb_etl", ROOT / "scripts" / "rfb_etl.py")
rfb = importlib.util.module_from_spec(_spec)
sys.modules["rfb_etl"] = rfb  # necessário para os workers (pickle) enxergarem as funções
_spec.loader.exec_module(rfb)


def _app_norm_name():
    """Extrai a `norm_name` REAL do app sem importar o pacote (que exigiria banco/config)."""
    src = (ROOT / "app" / "services" / "people.py").read_text(encoding="utf-8")
    import ast
    import unicodedata

    tree = ast.parse(src)
    wanted = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in ("strip_accents", "norm_name")]
    mod = ast.Module(body=wanted, type_ignores=[])
    ns = {"unicodedata": unicodedata, "Optional": __import__("typing").Optional}
    exec(compile(ast.fix_missing_locations(mod), "people.py", "exec"), ns)
    return ns["norm_name"]


def _line(*fields):
    return ";".join(f'"{f}"' for f in fields) + "\n"


def _zip(path: Path, member: str, text: str, encoding="iso-8859-1"):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(member, text.encode(encoding))
    return path


def _socio(basico, ident, nome, cpf, qual="49", data="20200131", faixa="5"):
    # 11 colunas; as de representante legal trazem dados que NÃO podem vazar
    return _line(basico, ident, nome, cpf, qual, data, "", "***111222**", "REPRESENTANTE SECRETO", "05", faixa)


def _estab(basico, ordem, ident, sit="02", uf="DF", fantasia="LOJA X", cnae="4711302"):
    cols = [""] * 30
    cols[0], cols[1], cols[2], cols[3], cols[4] = basico, ordem, "00", ident, fantasia
    cols[5], cols[6], cols[10], cols[11] = sit, "20200101", "20100505", cnae
    cols[14], cols[15] = "RUA SECRETA", "123"        # logradouro, número
    cols[18], cols[19], cols[20] = "70000000", uf, "9701"  # CEP, UF, município
    cols[21], cols[22] = "61", "999990000"           # DDD/telefone
    cols[27] = "segredo@exemplo.com"                 # e-mail
    return _line(*cols)


@pytest.fixture
def keys_file(tmp_path):
    p = tmp_path / "keys.txt"
    p.write_text("JOAO DA SILVA|123456\nMARIA JOSE|654321\nJOSE NUNES|111111\n", encoding="utf-8")
    return str(p)


@pytest.fixture
def source_dir(tmp_path):
    """Diretório com ZIPs sintéticos de um mês inteiro (10 shards; só os 0 têm dados)."""
    d = tmp_path / "src"
    d.mkdir()
    socios = (
        _socio("00000001", "2", "João da  Silva", "***123456**")          # casa (acento/espaços/minúsculas normalizam)
        + _socio("00000002", "2", "JOAO DA SILVA", "***999999**")         # homônimo, CPF central diferente: NÃO casa
        + _socio("00000003", "1", "JOAO DA SILVA", "12345678000199")     # PJ: ignorado
        + _socio("00000004", "2", "MARIA JOSE", "***65432**")             # 5 dígitos: ignorado
        + _socio("00000005", "2", "MARIA JOSE", "***000000**")            # 6 dígitos, mas zeros/outro: NÃO casa
        + _socio("00000006", "3", "JOSE NUNES", "***111111**")            # estrangeiro: ignorado
        + _socio("00000007", "2", "MARIA JOSÉ", "***654321**")            # casa (acento)
    )
    _zip(d / "Socios0.zip", "K.SOCIOCSV", socios)
    for i in range(1, 10):
        _zip(d / f"Socios{i}.zip", "K.SOCIOCSV", "")
    emp = (
        _line("00000001", "PADARIA ÇÃO LTDA", "2062", "49", "1500,50", "03", "")
        + _line("00000007", "MARIA JOSÉ ME", "2135", "49", "0,00", "01", "")
        + _line("00000099", "NAO CASADA SA", "2046", "49", "9,00", "05", "")
    )
    _zip(d / "Empresas0.zip", "K.EMPRECSV", emp)
    est = (
        _estab("00000001", "0001", "1", fantasia="PADARIA DO ZÉ")
        + _estab("00000001", "0002", "2", uf="SP", fantasia="FILIAL")        # filial: ignorada
        + _estab("00000007", "0001", "1", sit="08", uf="GO")
        + _estab("00000099", "0001", "1")                                    # CNPJ não casado
    )
    _zip(d / "Estabelecimentos0.zip", "K.ESTABELE", est)
    for i in range(1, 10):
        _zip(d / f"Empresas{i}.zip", "K.EMPRECSV", "")
        _zip(d / f"Estabelecimentos{i}.zip", "K.ESTABELE", "")
    _zip(d / "Qualificacoes.zip", "Q.CSV", _line("49", "Sócio-Administrador") + _line("00", "Não informada"))
    _zip(d / "Naturezas.zip", "N.CSV", _line("2062", "Sociedade Empresária Limitada"))
    _zip(d / "Cnaes.zip", "C.CSV", _line("4711302", "Comércio varejista"))
    return d


def _read(path):
    with open(path, encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def _run(source_dir, keys_file, out, **kw):
    argv = ["run", "--month", "2026-10", "--keys", keys_file, "--out", str(out),
            "--source-dir", str(source_dir), "--workers", "2"]
    for k, v in kw.items():
        argv += [f"--{k.replace('_', '-')}", str(v)]
    return rfb.main(argv)


# ───────────────────────── normalização ─────────────────────────

@pytest.mark.parametrize("texto", [
    None, "", "  ", "João  da Silva", "MARIA JOSÉ", "ÀÉÎÕÜ ç Ñ", "  espaços\tmistos\n aqui ",
    "Ação & Cia", "ǅ ligature ﬁ", "Çağlar Öztürk",
])
def test_norm_name_identica_ao_app(texto):
    assert rfb.norm_name(texto) == _app_norm_name()(texto)


def test_cpf_core_exige_seis_digitos():
    assert rfb.cpf_core("***123456**") == "123456"
    assert rfb.cpf_core("***.411.707-**") == "411707"
    assert rfb.cpf_core("***12345**") is None
    assert rfb.cpf_core("12345678901") is None
    assert rfb.cpf_core("") is None and rfb.cpf_core(None) is None


def test_formatadores():
    assert rfb.fmt_date("20200131") == "2020-01-31"
    assert rfb.fmt_date("00000000") == "" and rfb.fmt_date("0") == "" and rfb.fmt_date("") == ""
    assert rfb.fmt_date("20201340") == ""
    assert rfb.fmt_decimal("120000000000,00") == "120000000000.00"
    assert rfb.fmt_decimal("") == ""


# ───────────────────────── fluxo completo ─────────────────────────

def test_run_completo_filtra_e_respeita_contrato(source_dir, keys_file, tmp_path):
    out = tmp_path / "out"
    assert _run(source_dir, keys_file, out) == 0

    socios = _read(out / "socios.csv")
    assert list(socios[0].keys()) == rfb.SOCIOS_HEADER
    assert [(s["cnpj_basico"], s["nome_norm"], s["cpf_core"]) for s in socios] == [
        ("00000001", "JOAO DA SILVA", "123456"),
        ("00000007", "MARIA JOSE", "654321"),
    ]
    assert socios[0]["nome_socio"] == "João da  Silva"  # nome original preservado (UTF-8)
    assert socios[0]["data_entrada"] == "2020-01-31" and socios[0]["qualificacao_cod"] == "49"
    assert socios[1]["nome_socio"] == "MARIA JOSÉ"  # latin-1 -> UTF-8 correto

    empresas = _read(out / "empresas.csv")
    assert list(empresas[0].keys()) == rfb.EMPRESAS_HEADER
    assert [e["cnpj_basico"] for e in empresas] == ["00000001", "00000007"]
    e1 = empresas[0]
    assert e1["razao_social"] == "PADARIA ÇÃO LTDA"
    assert e1["capital_social"] == "1500.50"
    assert e1["uf"] == "DF" and e1["nome_fantasia"] == "PADARIA DO ZÉ"  # veio da matriz, não da filial
    assert e1["data_inicio_atividade"] == "2010-05-05" and e1["data_situacao"] == "2020-01-01"
    assert e1["situacao_cadastral_cod"] == "02" and e1["cnae_principal"] == "4711302"
    assert empresas[1]["uf"] == "GO" and empresas[1]["situacao_cadastral_cod"] == "08"

    assert _read(out / "qualificacoes.csv")[0] == {"cod": "49", "descricao": "Sócio-Administrador"}
    assert _read(out / "naturezas.csv")[0]["descricao"] == "Sociedade Empresária Limitada"
    assert _read(out / "cnaes.csv")[0]["cod"] == "4711302"

    man = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert man["mes"] == "2026-10" and man["completo"] is True and man["layout_validado"]
    assert man["contagens"]["socios_casados"] == 2
    for nome, info in man["arquivos"].items():
        assert info["sha256"] == rfb.sha256_file(str(out / nome))
    assert not [p for p in os.listdir(out) if p.endswith(".tmp")]


def test_nenhum_campo_proibido_nas_saidas(source_dir, keys_file, tmp_path):
    out = tmp_path / "out"
    _run(source_dir, keys_file, out)
    conteudo = ""
    for p in out.iterdir():
        if p.suffix == ".csv":
            conteudo += p.read_text(encoding="utf-8")
            cab = p.read_text(encoding="utf-8").splitlines()[0].lower()
            # `cpf_core` (6 dígitos centrais) é permitido pelo contrato; qualquer outro "cpf" não
            colunas = [c for c in cab.split(",") if c != "cpf_core"]
            assert not any(proibido in c for c in colunas for proibido in rfb.CAMPOS_PROIBIDOS), (p.name, cab)
    for segredo in ("REPRESENTANTE SECRETO", "***111222**", "111222", "RUA SECRETA", "70000000",
                    "999990000", "segredo@exemplo.com", "@"):
        assert segredo not in conteudo
    assert "***" not in conteudo  # nenhum CPF mascarado completo


def test_max_shards_marca_incompleto(source_dir, keys_file, tmp_path):
    out = tmp_path / "out"
    assert _run(source_dir, keys_file, out, max_shards=1) == 0
    man = json.loads((out / "manifest.json").read_text())
    assert man["completo"] is False and man["shards_processados"] == [0]


def test_cnaes_ausente_nao_quebra(source_dir, keys_file, tmp_path):
    (source_dir / "Cnaes.zip").unlink()
    out = tmp_path / "out"
    assert _run(source_dir, keys_file, out) == 0
    assert not (out / "cnaes.csv").exists()


# ───────────────────────── falhas e atomicidade ─────────────────────────

def test_zip_truncado_levanta_etlerror(source_dir, keys_file, tmp_path):
    z = source_dir / "Socios3.zip"
    z.write_bytes(z.read_bytes()[:20])
    out = tmp_path / "out"
    with pytest.raises(rfb.EtlError):
        args = rfb.build_parser().parse_args(["run", "--month", "2026-10", "--keys", keys_file,
                                              "--out", str(out), "--source-dir", str(source_dir)])
        args.fn(args)
    assert not (out / "manifest.json").exists() and not (out / "socios.csv").exists()


def test_crc_corrompido_levanta_etlerror(tmp_path):
    z = tmp_path / "Socios0.zip"
    with zipfile.ZipFile(z, "w", zipfile.ZIP_STORED) as zf:
        zf.writestr("a.csv", ("x" * 500).encode())
    raw = bytearray(z.read_bytes())
    raw[60] ^= 0xFF  # corrompe dado dentro do membro
    z.write_bytes(bytes(raw))
    with zipfile.ZipFile(z) as zf:
        with pytest.raises(rfb.EtlError):
            list(rfb.iter_rows(zf, "dominio", "Socios0.zip"))


def test_layout_inesperado_falha(tmp_path):
    z = _zip(tmp_path / "S.zip", "a.csv", _line("1", "2", "3"))
    with zipfile.ZipFile(z) as zf:
        with pytest.raises(rfb.EtlError, match="layout"):
            list(rfb.iter_rows(zf, "socios", "S.zip"))


def test_atomic_path_remove_tmp_em_erro(tmp_path):
    final = tmp_path / "x.csv"
    with pytest.raises(RuntimeError):
        with rfb.atomic_path(str(final)) as tmp:
            Path(tmp).write_text("parcial")
            raise RuntimeError("boom")
    assert not final.exists() and not Path(str(final) + ".tmp").exists()
    with rfb.atomic_path(str(final)) as tmp:
        Path(tmp).write_text("ok")
    assert final.read_text() == "ok" and not Path(str(final) + ".tmp").exists()


def test_workers_limitado_a_tres():
    args = rfb.build_parser().parse_args(["run", "--month", "2026-10", "--keys", "k", "--out", "o", "--workers", "9"])
    assert max(1, min(args.workers, rfb.MAX_WORKERS)) == 3


def test_zip_baixado_e_apagado(tmp_path, monkeypatch):
    """Quando baixa (sem --source-dir), o ZIP é removido ao sair, inclusive em erro."""
    def fake_download(month, name, dest):
        _zip(Path(dest), "a.csv", _line("1", "Nome"))
        return 1
    monkeypatch.setattr(rfb, "download", fake_download)
    work = tmp_path / "w"
    work.mkdir()
    with pytest.raises(RuntimeError):
        with rfb.open_zip("2026-10", "Cnaes.zip", str(work), None) as zf:
            assert os.listdir(work)
            raise RuntimeError("falha no processamento")
    assert os.listdir(work) == []


def test_load_keys_ignora_linhas_invalidas(tmp_path):
    p = tmp_path / "k.txt"
    p.write_text("A B|123456\nsem-pipe\nC D|12345\n|123456\nE|F|654321\n\n", encoding="utf-8")
    keys, cores = rfb.load_keys(str(p))
    assert keys == {"A B|123456", "E|F|654321"} and cores == {"123456", "654321"}

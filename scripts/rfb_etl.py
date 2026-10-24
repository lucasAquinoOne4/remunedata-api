#!/usr/bin/env python3
"""
ETL local dos dados abertos de CNPJ da Receita Federal (sociedades de pessoas já presentes no portal).

Regra de minimização de dados (LGPD): baixamos os ZIPs oficiais, FILTRAMOS localmente e gravamos só
o que casa com pessoas que já existem em `person_latest`. Casamento: nome IDÊNTICO após normalização
(igual a `norm_name` de app/services/people.py) E os mesmos 6 dígitos centrais do CPF, só sócios PF.
Nunca é gravado: CPF completo, endereço, telefone, e-mail, representante legal.

Somente biblioteca padrão (o subcomando `keys` importa psycopg2 sob demanda, já presente na imagem).

Subcomandos:
  keys     gera o arquivo de chaves `NOME NORMALIZADO|123456` a partir de person_latest
  run      baixa, filtra e grava os CSVs compactos + manifest.json
  inspect  lista o que há no compartilhamento ou mostra as primeiras linhas de um ZIP local

Exemplo:
  python scripts/rfb_etl.py keys --out /tmp/keys.txt
  python scripts/rfb_etl.py run --month 2026-10 --keys /tmp/keys.txt --out /data/rfb_out --workers 3
"""
from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import io
import json
import os
import re
import shutil
import sys
import tempfile
import time
import unicodedata
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
import zlib
import zipfile
from concurrent.futures import ProcessPoolExecutor, as_completed
from contextlib import contextmanager
from typing import Dict, Iterable, Iterator, List, Optional, Set, Tuple

# ───────────────────────── constantes ─────────────────────────

LAYOUT_VERSION = "rfb-cnpj-2026-10"  # layout validado contra amostra real de 2026-10
BASE_URL = "https://arquivos.receitafederal.gov.br/public.php/webdav"
SHARE_TOKEN = "YggdBLfdninEJX9"  # token público do compartilhamento (usuário; senha vazia)
USER_AGENT = "remunedata-api-etl/1.0 (dados abertos RFB; minimizacao LGPD; contato: projeto remunedata)"
SHARDS = 10
MAX_WORKERS = 3  # teto de downloads simultâneos
RETRIES = 5
ENCODING = "iso-8859-1"

# número de colunas esperado por tipo de arquivo (validação do layout)
N_COLS = {"socios": 11, "empresas": 7, "estabelecimentos": 30, "dominio": 2}

SOCIOS_HEADER = ["cnpj_basico", "nome_socio", "nome_norm", "cpf_core", "qualificacao_cod", "data_entrada", "faixa_etaria"]
EMPRESAS_HEADER = [
    "cnpj_basico", "razao_social", "natureza_juridica_cod", "capital_social", "porte_cod",
    "situacao_cadastral_cod", "data_situacao", "data_inicio_atividade", "cnae_principal",
    "uf", "municipio_cod", "nome_fantasia",
]
DOMINIO_HEADER = ["cod", "descricao"]

# campos que NUNCA podem aparecer nas saídas (usado também nos testes)
CAMPOS_PROIBIDOS = (
    "cpf", "logradouro", "numero", "complemento", "bairro", "cep", "telefone", "ddd", "fax",
    "correio_eletronico", "email", "representante",
)


class EtlError(RuntimeError):
    """Erro de execução do ETL (download, ZIP corrompido, layout inesperado)."""


def log(msg: str) -> None:
    print(time.strftime("%H:%M:%S"), msg, file=sys.stderr, flush=True)


# ───────────────────────── normalização (idêntica a app/services/people.py) ─────────────────────────

def strip_accents(text: str) -> str:
    return "".join(
        ch for ch in unicodedata.normalize("NFKD", text) if not unicodedata.combining(ch)
    )


def norm_name(text: Optional[str]) -> str:
    """Maiúsculas, sem acentos, espaços colapsados. Cópia fiel de `norm_name` do app."""
    return " ".join(strip_accents(text or "").upper().split())


def cpf_core(cpf: Optional[str]) -> Optional[str]:
    """6 dígitos centrais do CPF mascarado ('***927115**'); None se não houver exatamente 6 dígitos."""
    digits = re.sub(r"\D", "", cpf or "")
    return digits if len(digits) == 6 else None


def make_key(nome: Optional[str], core: str) -> str:
    return f"{norm_name(nome)}|{core}"


def fmt_date(raw: str) -> str:
    """AAAAMMDD -> AAAA-MM-DD; vazio para '', '0', '00000000' ou data inválida."""
    raw = (raw or "").strip()
    if len(raw) != 8 or not raw.isdigit() or raw == "00000000":
        return ""
    y, m, d = raw[:4], raw[4:6], raw[6:]
    if not ("01" <= m <= "12" and "01" <= d <= "31" and y != "0000"):
        return ""
    return f"{y}-{m}-{d}"


def fmt_decimal(raw: str) -> str:
    """'1234,50' -> '1234.50' (vírgula decimal; sem separador de milhar na origem)."""
    raw = (raw or "").strip().replace(".", "").replace(",", ".")
    if not raw:
        return ""
    try:
        float(raw)
    except ValueError:
        return ""
    return raw


# ───────────────────────── chaves ─────────────────────────

def load_keys(path: str) -> Tuple[Set[str], Set[str]]:
    """Lê o arquivo de chaves. Devolve (chaves completas, só os 6 dígitos — pré-filtro barato)."""
    keys: Set[str] = set()
    cores: Set[str] = set()
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.rstrip("\n")
            if not line:
                continue
            nome, sep, core = line.rpartition("|")
            if not sep or len(core) != 6 or not core.isdigit() or not nome:
                continue
            keys.add(f"{nome}|{core}")
            cores.add(core)
    return keys, cores


def cmd_keys(args: argparse.Namespace) -> int:
    """Gera o arquivo de chaves a partir de person_latest (cpf com exatamente 6 dígitos)."""
    url = args.db_url or os.environ.get("DATABASE_URL_SYNC")
    if not url:
        raise EtlError("DATABASE_URL_SYNC não definida (ou use --db-url)")
    url = re.sub(r"^(postgres(?:ql)?)\+\w+://", r"\1://", url)  # psycopg2 não entende o sufixo +driver
    try:
        import psycopg2  # type: ignore
    except ImportError as exc:  # pragma: no cover
        raise EtlError("psycopg2 indisponível; rode dentro do container app") from exc
    # SQL fixo, sem valores interpolados: a limpeza do CPF é feita no próprio banco.
    sql = (
        "SELECT DISTINCT nome, regexp_replace(cpf, '\\D', '', 'g') AS core "
        "FROM person_latest "
        "WHERE cpf IS NOT NULL AND nome IS NOT NULL "
        "AND length(regexp_replace(cpf, '\\D', '', 'g')) = 6"
    )
    keys: Set[str] = set()
    conn = psycopg2.connect(url)
    try:
        conn.set_session(readonly=True)
        with conn.cursor(name="rfb_keys") as cur:  # cursor server-side: não carrega tudo
            cur.itersize = 50000
            cur.execute(sql)
            for nome, core in cur:
                n = norm_name(nome)
                if n:
                    keys.add(f"{n}|{core}")
    finally:
        conn.close()
    with atomic_path(args.out) as tmp:
        with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
            for k in sorted(keys):
                fh.write(k + "\n")
    log(f"{len(keys)} chaves únicas gravadas em {args.out}")
    return 0


# ───────────────────────── escrita atômica ─────────────────────────

@contextmanager
def atomic_path(final: str) -> Iterator[str]:
    """Entrega um caminho .tmp; só renomeia para `final` se o bloco terminar sem erro."""
    tmp = final + ".tmp"
    try:
        yield tmp
        os.replace(tmp, final)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ───────────────────────── acesso à Receita (WebDAV público) ─────────────────────────

def _auth_header() -> str:
    return "Basic " + base64.b64encode(f"{SHARE_TOKEN}:".encode()).decode()


def _request(url: str, method: str = "GET", headers: Optional[dict] = None) -> urllib.request.Request:
    h = {"User-Agent": USER_AGENT, "Authorization": _auth_header()}
    h.update(headers or {})
    return urllib.request.Request(url, method=method, headers=h)


def propfind(path: str = "") -> List[Tuple[str, int]]:
    """Lista (nome, tamanho) do diretório WebDAV (Depth: 1)."""
    url = f"{BASE_URL}/{path}".rstrip("/") + "/"
    last: Optional[Exception] = None
    for attempt in range(RETRIES):
        try:
            with urllib.request.urlopen(_request(url, "PROPFIND", {"Depth": "1"}), timeout=60) as r:
                body = r.read()
            break
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            last = exc
            time.sleep(2 ** attempt)
    else:
        raise EtlError(f"PROPFIND falhou em {url}: {last}")
    ns = {"d": "DAV:"}
    out = []
    for resp in ET.fromstring(body).findall("d:response", ns):
        href = resp.findtext("d:href", default="", namespaces=ns).rstrip("/")
        size = resp.findtext(".//d:getcontentlength", default="0", namespaces=ns)
        name = urllib.request.url2pathname(href.rsplit("/", 1)[-1])
        if name and name != "webdav":
            out.append((name, int(size or 0)))
    return out


def download(month: str, name: str, dest: str) -> int:
    """Baixa `month/name` para `dest` (via .part), com retentativas/backoff. Confere o tamanho."""
    url = f"{BASE_URL}/{month}/{name}"
    last: Optional[Exception] = None
    for attempt in range(RETRIES):
        part = dest + ".part"
        try:
            with urllib.request.urlopen(_request(url), timeout=120) as r, open(part, "wb") as out:
                expected = int(r.headers.get("Content-Length") or 0)
                got = 0
                while True:
                    chunk = r.read(1 << 20)
                    if not chunk:
                        break
                    out.write(chunk)
                    got += len(chunk)
            if expected and got != expected:
                raise EtlError(f"download truncado: {got} de {expected} bytes")
            os.replace(part, dest)
            return got
        except (urllib.error.URLError, TimeoutError, ConnectionError, EtlError) as exc:
            last = exc
            if isinstance(exc, urllib.error.HTTPError) and exc.code in (401, 403, 404):
                break  # não adianta insistir
            log(f"  tentativa {attempt + 1}/{RETRIES} de {name} falhou: {exc}")
            time.sleep(min(60, 2 ** attempt * 2))
        finally:
            if os.path.exists(part):
                os.remove(part)
    raise EtlError(f"não foi possível baixar {url}: {last}")


@contextmanager
def open_zip(month: str, name: str, workdir: str, source_dir: Optional[str]) -> Iterator[zipfile.ZipFile]:
    """Abre o ZIP (baixando para workdir ou lendo de source_dir) e APAGA o baixado ao sair."""
    if source_dir:
        path, temp = os.path.join(source_dir, name), False
        if not os.path.exists(path):
            raise EtlError(f"arquivo local ausente: {path}")
    else:
        path, temp = os.path.join(workdir, f"{month}_{name}"), True
        download(month, name, path)
    try:
        try:
            zf = zipfile.ZipFile(path)
        except zipfile.BadZipFile as exc:
            raise EtlError(f"ZIP ilegível/truncado: {name} ({exc})") from exc
        with zf:
            yield zf
    finally:
        if temp and os.path.exists(path):
            os.remove(path)


def iter_rows(zf: zipfile.ZipFile, kind: str, name: str) -> Iterator[List[str]]:
    """Linhas (listas de campos) de todos os membros do ZIP, em fluxo, validando o nº de colunas.
    Erros de CRC/truncamento do ZIP viram EtlError (nada de saída parcial)."""
    ncols = N_COLS[kind]
    try:
        for info in zf.infolist():
            if info.is_dir():
                continue
            with zf.open(info) as raw:
                text = io.TextIOWrapper(raw, encoding=ENCODING, newline="")
                for row in csv.reader(text, delimiter=";", quotechar='"'):
                    if not row:
                        continue
                    if len(row) != ncols:
                        raise EtlError(
                            f"layout inesperado em {name}: {len(row)} colunas (esperado {ncols}); "
                            f"o layout da Receita pode ter mudado"
                        )
                    yield row
    except (zipfile.BadZipFile, EOFError, zlib.error) as exc:
        raise EtlError(f"ZIP corrompido em {name}: {exc}") from exc



# ───────────────────────── workers (nível de módulo: picláveis) ─────────────────────────

_KEYS: Set[str] = set()
_CORES: Set[str] = set()


def _init_keys(keys_path: str) -> None:
    global _KEYS, _CORES
    _KEYS, _CORES = load_keys(keys_path)


def _write_part(path: str, rows: Iterable[List[str]]) -> int:
    n = 0
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        for r in rows:
            w.writerow(r)
            n += 1
    return n


def process_socios(zf: zipfile.ZipFile, name: str, keys: Set[str], cores: Set[str]) -> Tuple[List[List[str]], int]:
    """Filtra sócios PF cujo (nome normalizado, 6 dígitos) está em `keys`. Devolve (linhas, lidas)."""
    out: List[List[str]] = []
    lidas = 0
    for r in iter_rows(zf, "socios", name):
        lidas += 1
        if r[1] != "2":  # só pessoa física
            continue
        core = cpf_core(r[3])
        if core is None or core not in cores:  # pré-filtro barato antes de normalizar o nome
            continue
        nome_norm = norm_name(r[2])
        if f"{nome_norm}|{core}" not in keys:
            continue
        # r[7..9] (CPF/nome/qualificação do representante legal) são descartados de propósito
        out.append([r[0], r[2].strip(), nome_norm, core, r[4], fmt_date(r[5]), r[10]])
    return out, lidas


def process_empresas(zf: zipfile.ZipFile, name: str, basicos: Set[str]) -> Tuple[List[List[str]], int]:
    out: List[List[str]] = []
    lidas = 0
    for r in iter_rows(zf, "empresas", name):
        lidas += 1
        if r[0] in basicos:
            out.append([r[0], r[1].strip(), r[2], fmt_decimal(r[4]), r[5]])
    return out, lidas


def process_estab(zf: zipfile.ZipFile, name: str, basicos: Set[str]) -> Tuple[List[List[str]], int]:
    """Só matrizes (identificador=1 ou ordem 0001) de CNPJs casados. Descarta endereço/contato."""
    out: List[List[str]] = []
    lidas = 0
    for r in iter_rows(zf, "estabelecimentos", name):
        lidas += 1
        if r[0] not in basicos:
            continue
        if not (r[3] == "1" or r[1] == "0001"):
            continue
        # campos aproveitados: situação (5), data situação (6), início atividade (10), CNAE (11),
        # UF (19), município (20), nome fantasia (4). O restante (endereço, telefone, e-mail) é ignorado.
        out.append([r[0], r[5], fmt_date(r[6]), fmt_date(r[10]), r[11], r[19], r[20], r[4].strip()])
    return out, lidas


def process_dominio(zf: zipfile.ZipFile, name: str) -> List[List[str]]:
    return [[r[0], r[1].strip()] for r in iter_rows(zf, "dominio", name)]


def shard_task(kind: str, shard: int, month: str, workdir: str, source_dir: Optional[str],
               basicos: Optional[Set[str]]) -> dict:
    """Processa um shard completo: baixa, filtra, grava o parcial em workdir e apaga o ZIP."""
    prefix = {"socios": "Socios", "empresas": "Empresas", "estabelecimentos": "Estabelecimentos"}[kind]
    name = f"{prefix}{shard}.zip"
    t0 = time.time()
    with open_zip(month, name, workdir, source_dir) as zf:
        t_dl = time.time() - t0
        t1 = time.time()
        if kind == "socios":
            rows, lidas = process_socios(zf, name, _KEYS, _CORES)
        elif kind == "empresas":
            rows, lidas = process_empresas(zf, name, basicos or set())
        else:
            rows, lidas = process_estab(zf, name, basicos or set())
    part = os.path.join(workdir, f"{kind}_{shard}.part.csv")
    n = _write_part(part, rows)
    dt = time.time() - t1
    return {"kind": kind, "shard": shard, "part": part, "linhas_lidas": lidas, "linhas_saida": n,
            "seg_download": round(t_dl, 1), "seg_processo": round(dt, 1),
            "linhas_por_seg": int(lidas / dt) if dt > 0 else 0}


def _read_part(path: str) -> Iterator[List[str]]:
    with open(path, encoding="utf-8", newline="") as fh:
        yield from csv.reader(fh)


# ───────────────────────── orquestração ─────────────────────────

def run_stage(kind: str, shards: List[int], workers: int, month: str, workdir: str,
              source_dir: Optional[str], keys_path: str, basicos: Optional[Set[str]]) -> List[dict]:
    log(f"etapa {kind}: {len(shards)} shard(s), {workers} worker(s)")
    results: List[dict] = []
    with ProcessPoolExecutor(max_workers=workers, initializer=_init_keys if kind == "socios" else None,
                             initargs=(keys_path,) if kind == "socios" else ()) as ex:
        futs = {ex.submit(shard_task, kind, s, month, workdir, source_dir, basicos): s for s in shards}
        for fut in as_completed(futs):
            res = fut.result()  # qualquer erro de shard aborta a execução inteira
            log(f"  {kind}{res['shard']}: {res['linhas_lidas']} lidas, {res['linhas_saida']} mantidas, "
                f"{res['linhas_por_seg']} linhas/s (download {res['seg_download']}s)")
            results.append(res)
    return sorted(results, key=lambda r: r["shard"])


def write_csv(path: str, header: List[str], rows: Iterable[List[str]]) -> int:
    n = 0
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(header)
        for r in rows:
            w.writerow(r)
            n += 1
    return n


def cmd_run(args: argparse.Namespace) -> int:
    if not re.fullmatch(r"\d{4}-\d{2}", args.month):
        raise EtlError("--month deve ser AAAA-MM")
    workers = max(1, min(args.workers, MAX_WORKERS))
    shards = list(range(SHARDS))[: args.max_shards] if args.max_shards else list(range(SHARDS))
    out_dir = os.path.abspath(args.out)
    os.makedirs(out_dir, exist_ok=True)
    manifest_path = os.path.join(out_dir, "manifest.json")
    if os.path.exists(manifest_path):
        os.remove(manifest_path)  # a presença do manifest indica execução COMPLETA
    workdir = tempfile.mkdtemp(prefix="rfb_work_", dir=args.workdir or out_dir)
    keys, _ = load_keys(args.keys)
    if not keys:
        raise EtlError("arquivo de chaves vazio ou inválido")
    log(f"{len(keys)} chaves; mês {args.month}; shards {shards}")
    t_start = time.time()
    stats: Dict[str, object] = {"chaves": len(keys)}
    try:
        # (1) sócios PF casados
        soc = run_stage("socios", shards, workers, args.month, workdir, args.source_dir, args.keys, None)
        basicos: Set[str] = set()
        seen: Set[Tuple[str, ...]] = set()
        socios_rows: List[List[str]] = []
        for res in soc:
            for r in _read_part(res["part"]):
                t = tuple(r)
                if t not in seen:
                    seen.add(t)
                    socios_rows.append(r)
                    basicos.add(r[0])
        socios_rows.sort(key=lambda r: (r[0], r[2], r[3]))
        stats["socios_linhas_lidas"] = sum(r["linhas_lidas"] for r in soc)
        stats["socios_casados"] = len(socios_rows)
        stats["empresas_casadas_basicos"] = len(basicos)
        del seen

        # (2) empresas dos CNPJs casados
        emp = run_stage("empresas", shards, workers, args.month, workdir, args.source_dir, args.keys, basicos)
        empresas: Dict[str, List[str]] = {}
        for res in emp:
            for r in _read_part(res["part"]):
                empresas[r[0]] = r
        stats["empresas_linhas_lidas"] = sum(r["linhas_lidas"] for r in emp)
        stats["empresas_encontradas"] = len(empresas)

        # (3) estabelecimentos (matriz) mesclados às empresas
        est = run_stage("estabelecimentos", shards, workers, args.month, workdir, args.source_dir, args.keys, basicos)
        matriz: Dict[str, List[str]] = {}
        for res in est:
            for r in _read_part(res["part"]):
                matriz[r[0]] = r
        stats["estabelecimentos_linhas_lidas"] = sum(r["linhas_lidas"] for r in est)
        stats["estabelecimentos_matriz"] = len(matriz)

        empresas_rows = []
        for b in sorted(empresas):
            e = empresas[b]  # basico, razao, natureza, capital, porte
            m = matriz.get(b, [b, "", "", "", "", "", "", ""])  # basico, sit, dt_sit, dt_ini, cnae, uf, mun, fantasia
            empresas_rows.append([b, e[1], e[2], e[3], e[4], m[1], m[2], m[3], m[4], m[5], m[6], m[7]])

        # (4) tabelas de domínio pequenas
        dominios: Dict[str, List[List[str]]] = {}
        for fname, arq, obrigatorio in (("qualificacoes.csv", "Qualificacoes.zip", True),
                                        ("naturezas.csv", "Naturezas.zip", True),
                                        ("cnaes.csv", "Cnaes.zip", False)):
            try:
                with open_zip(args.month, arq, workdir, args.source_dir) as zf:
                    dominios[fname] = process_dominio(zf, arq)
            except EtlError as exc:
                if obrigatorio:
                    raise
                log(f"aviso: {arq} indisponível, {fname} não será gerado ({exc})")

        # (5) publica tudo de uma vez (cada arquivo via .tmp + rename) e por último o manifest
        outputs: Dict[str, dict] = {}

        def publish(fname: str, header: List[str], rows: Iterable[List[str]]) -> None:
            final = os.path.join(out_dir, fname)
            with atomic_path(final) as tmp:
                n = write_csv(tmp, header, rows)
            outputs[fname] = {"linhas": n, "sha256": sha256_file(final)}

        publish("socios.csv", SOCIOS_HEADER, socios_rows)
        publish("empresas.csv", EMPRESAS_HEADER, empresas_rows)
        for fname, rows in dominios.items():
            publish(fname, DOMINIO_HEADER, rows)

        elapsed = time.time() - t_start
        manifest = {
            "mes": args.month,
            "layout_validado": LAYOUT_VERSION,
            "shards_processados": shards,
            "completo": len(shards) == SHARDS,
            "gerado_em": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "duracao_seg": round(elapsed, 1),
            "contagens": stats,
            "arquivos": outputs,
            "detalhe_shards": {k: [{x: r[x] for x in ("shard", "linhas_lidas", "linhas_saida", "linhas_por_seg",
                                                       "seg_download", "seg_processo")} for r in v]
                               for k, v in (("socios", soc), ("empresas", emp), ("estabelecimentos", est))},
        }
        with atomic_path(manifest_path) as tmp:
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(manifest, fh, ensure_ascii=False, indent=2)
        log(f"concluído em {elapsed:.0f}s: {json.dumps(stats)}")
        return 0
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


# ───────────────────────── inspect ─────────────────────────

def cmd_inspect(args: argparse.Namespace) -> int:
    if args.zip:
        kind = args.kind
        with zipfile.ZipFile(args.zip) as zf:
            for info in zf.infolist():
                print(f"# {info.filename} ({info.file_size} bytes)")
                with zf.open(info) as raw:
                    text = io.TextIOWrapper(raw, encoding=ENCODING, newline="")
                    for i, row in enumerate(csv.reader(text, delimiter=";")):
                        if i >= args.lines:
                            break
                        print(len(row), row if kind != "socios" else row[:3] + ["<mascarado>"] + row[4:7])
        return 0
    for name, size in propfind(args.month or ""):
        print(f"{name}\t{size}")
    return 0


# ───────────────────────── CLI ─────────────────────────

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    k = sub.add_parser("keys", help="gera NOME NORMALIZADO|123456 a partir de person_latest")
    k.add_argument("--out", required=True)
    k.add_argument("--db-url", default=None, help="padrão: env DATABASE_URL_SYNC")
    k.set_defaults(fn=cmd_keys)

    r = sub.add_parser("run", help="baixa, filtra e grava os CSVs")
    r.add_argument("--month", required=True, help="AAAA-MM")
    r.add_argument("--keys", required=True)
    r.add_argument("--out", required=True)
    r.add_argument("--workers", type=int, default=2, help=f"1..{MAX_WORKERS} (downloads simultâneos)")
    r.add_argument("--max-shards", type=int, default=0, help="só os K primeiros shards (testes)")
    r.add_argument("--workdir", default=None, help="onde ficam os ZIPs temporários (padrão: dentro de --out)")
    r.add_argument("--source-dir", default=None, help="lê ZIPs de um diretório local em vez de baixar (testes)")
    r.set_defaults(fn=cmd_run)

    i = sub.add_parser("inspect", help="lista arquivos do compartilhamento ou amostra um ZIP local")
    i.add_argument("--month", default=None)
    i.add_argument("--zip", default=None)
    i.add_argument("--kind", default="dominio", choices=list(N_COLS))
    i.add_argument("--lines", type=int, default=5)
    i.set_defaults(fn=cmd_inspect)
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.fn(args)
    except EtlError as exc:
        print(f"ERRO: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())

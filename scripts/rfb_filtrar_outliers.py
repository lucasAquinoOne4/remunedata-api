#!/usr/bin/env python3
"""
Pós-processa a saída de scripts/rfb_etl.py: remove pessoas com participações demais.

Quem aparece como sócio de mais de MAX_PARTICIPACOES empresas quase sempre é cadastro em massa ou
caso atípico, e exibir isso no perfil de um servidor (mesmo como cruzamento "provável") pode
atribuir algo grave a quem não tem relação. O filtro é conservador: a pessoa sai da carga e a
contagem fica registrada no manifest.json. Só biblioteca padrão.

Uso: rfb_filtrar_outliers.py ENTRADA SAIDA [--max 100]
"""

import argparse
import csv
import hashlib
import json
import shutil
from collections import Counter
from pathlib import Path


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def filtrar(entrada: Path, saida: Path, maximo: int) -> dict:
    saida.mkdir(parents=True, exist_ok=True)
    with (entrada / "socios.csv").open(encoding="utf-8", newline="") as fh:
        socios = list(csv.DictReader(fh))
        colunas_s = list(socios[0].keys()) if socios else []
    por_pessoa = Counter((r["nome_norm"], r["cpf_core"]) for r in socios)
    excluidas = {k for k, n in por_pessoa.items() if n > maximo}
    mantidos = [r for r in socios if (r["nome_norm"], r["cpf_core"]) not in excluidas]
    basicos = {r["cnpj_basico"] for r in mantidos}

    with (saida / "socios.csv.tmp").open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=colunas_s)
        w.writeheader()
        w.writerows(mantidos)
    n_emp = 0
    with (entrada / "empresas.csv").open(encoding="utf-8", newline="") as src, (saida / "empresas.csv.tmp").open(
        "w", encoding="utf-8", newline=""
    ) as dst:
        reader = csv.DictReader(src)
        w = csv.DictWriter(dst, fieldnames=reader.fieldnames)
        w.writeheader()
        for r in reader:
            if r["cnpj_basico"] in basicos:
                w.writerow(r)
                n_emp += 1
    (saida / "socios.csv.tmp").rename(saida / "socios.csv")
    (saida / "empresas.csv.tmp").rename(saida / "empresas.csv")
    for nome in ("qualificacoes.csv", "naturezas.csv", "cnaes.csv"):
        if (entrada / nome).exists():
            shutil.copy(entrada / nome, saida / nome)

    manifest = json.loads((entrada / "manifest.json").read_text(encoding="utf-8"))
    arquivos = {}
    for nome in ("socios.csv", "empresas.csv", "qualificacoes.csv", "naturezas.csv", "cnaes.csv"):
        p = saida / nome
        if p.exists():
            with p.open(encoding="utf-8") as fh:
                linhas = sum(1 for _ in fh) - 1
            arquivos[nome] = {"linhas": linhas, "sha256": _sha256(p)}
    manifest["arquivos"] = arquivos
    manifest["hashes"] = {n: a["sha256"] for n, a in arquivos.items()}
    manifest["filtro_pos_etl"] = {
        "max_participacoes_por_pessoa": maximo,
        "pessoas_excluidas": len(excluidas),
        "socios_removidos": len(socios) - len(mantidos),
    }
    manifest["filtro_pos_etl"]["empresas_mantidas"] = n_emp
    (saida / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest["filtro_pos_etl"] | {"socios_mantidos": len(mantidos)}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("entrada", type=Path)
    ap.add_argument("saida", type=Path)
    ap.add_argument("--max", type=int, default=100)
    a = ap.parse_args()
    print(json.dumps(filtrar(a.entrada, a.saida, a.max), ensure_ascii=False))

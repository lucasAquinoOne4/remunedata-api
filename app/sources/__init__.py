"""
Registro dos adaptadores de estados. Para adicionar um estado: crie app/sources/<uf>.py com um
objeto ADAPTER (ver base.py) e inclua o módulo em MODULES. Módulos ausentes são ignorados, para
que cada adaptador possa ser entregue de forma independente.
"""

import importlib
from typing import Dict

from app.sources.base import SourceAdapter

MODULES = ("ce", "pe", "go", "sc", "ju")


def load_adapters() -> Dict[str, SourceAdapter]:
    adapters: Dict[str, SourceAdapter] = {}
    for name in MODULES:
        try:
            module = importlib.import_module(f"app.sources.{name}")
        except ModuleNotFoundError as exc:
            if exc.name == f"app.sources.{name}":
                continue  # adaptador ainda não entregue
            raise
        adapter = getattr(module, "ADAPTER", None)
        if adapter is not None:
            adapters[adapter.uf] = adapter
    return adapters


ADAPTERS: Dict[str, SourceAdapter] = load_adapters()
STATE_UFS = tuple(name.upper() for name in MODULES)  # UFs previstas

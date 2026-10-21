"""
app/api/schemas.py
Modelos Pydantic para validação e serialização de dados da API.
"""

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict


class ExecutionMonthlyRead(BaseModel):
    id: int
    mes_referencia: str
    status: str
    paginas_consumidas: int
    registros_coletados: int
    started_at: datetime
    finished_at: Optional[datetime] = None
    error_message: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class ExecutionAnnualRead(BaseModel):
    """Schema para listagem (sem carregar meses)."""

    id: int
    ano_exercicio: int
    status: str
    started_at: datetime
    finished_at: Optional[datetime] = None
    duration_ms: Optional[int] = None
    total_meses_processados: int
    total_paginas_consumidas: int
    total_registros_coletados: int
    error_message: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class ExecutionAnnualDetail(ExecutionAnnualRead):
    """Schema para detalhe (inclui meses)."""

    monthly_executions: List[ExecutionMonthlyRead]


class RemunerationRead(BaseModel):
    id: int
    fonte: str = "DF"
    ano_exercicio: int
    mes_referencia: str
    codigo_identificacao: str
    codigo_matricula: str
    nome_servidor: str
    cpf_servidor: Optional[str] = None
    codigo_orgao: Optional[str] = None
    nome_orgao: Optional[str] = None
    cargo: Optional[str] = None
    valor_bruto: float
    valor_liquido: float
    created_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class PaginatedRemuneration(BaseModel):
    items: List[RemunerationRead]
    total: int
    total_capped: bool = False
    page: int
    size: int
    pages: int

"""
app/collector/annual.py
Orquestrador de coleta anual via Repositórios.
"""

import collections
from datetime import datetime, timezone

from app.collector.monthly import MonthlyCollector
from app.core.config import settings
from app.core.logging import get_logger
from app.persistence.models import ExecutionAnnual
from app.persistence.repositories import ExecutionRepository

logger = get_logger(__name__)


class AnnualCollector:
    """
    Orquestra a coleta de um ano completo (01 a 12) usando repositórios.
    """

    def __init__(self, monthly_collector: MonthlyCollector, execution_repo: ExecutionRepository):
        self.monthly_collector = monthly_collector
        self.execution_repo = execution_repo

    async def run(self, ano: int) -> ExecutionAnnual:
        """
        Executa a coleta para todos os 12 meses do ano informado.
        """
        logger.info(f"Iniciando orquestração anual para o exercício {ano}")

        # 1. Iniciar registro anual via Repo
        annual_exec = await self.execution_repo.get_or_create_annual(ano)
        annual_exec.status = "running"
        annual_exec.started_at = datetime.now(timezone.utc)

        # Reset counters para refletir apenas meses que REALMENTE estão com status success
        from sqlalchemy import func, select

        from app.persistence.models import ExecutionMonthly

        stats_stmt = select(
            func.sum(ExecutionMonthly.paginas_consumidas),
            func.sum(ExecutionMonthly.registros_coletados),
            func.count(ExecutionMonthly.id),
        ).where(
            ExecutionMonthly.execution_id == annual_exec.id, ExecutionMonthly.status == "success"
        )
        stats_res = await self.execution_repo.session.execute(stats_stmt)
        p_sums, r_sums, m_count = stats_res.fetchone() or (0, 0, 0)

        annual_exec.total_paginas_consumidas = p_sums or 0
        annual_exec.total_registros_coletados = r_sums or 0
        annual_exec.total_meses_processados = m_count or 0

        await self.execution_repo.session.commit()

        meses = [f"{m:02d}" for m in range(1, 13)]
        if settings.transparencia_recent_months_first:
            meses.reverse()
        stats = collections.Counter()

        # 2. Iterar meses
        for mes in meses:
            try:
                # 2.1 Verificar se o mês já foi concluído com sucesso nesta execução
                existing_monthly = await self.execution_repo.get_monthly_execution_by_mes(
                    annual_exec.id, mes
                )
                # Mês concluído COM dados é pulado. Mês "success" com 0 registros significa que o
                # portal ainda não tinha publicado a competência: tenta de novo (1 requisição).
                if (
                    existing_monthly
                    and existing_monthly.status == "success"
                    and (existing_monthly.registros_coletados or 0) > 0
                ):
                    logger.info(f"Mês {mes}/{ano} já concluído com sucesso. Pulando...")
                    stats["success"] += 1
                    continue

                logger.info(f"Orquestrando mês {mes}/{ano}")
                # Mês "success" vazio já entrou na soma inicial de meses processados
                ja_contado = bool(existing_monthly and existing_monthly.status == "success")
                result = await self.monthly_collector.collect(
                    ano=ano, mes=mes, annual_execution_id=annual_exec.id
                )
                stats[result.status] += 1

                if result.status == "success" and not ja_contado:
                    annual_exec.total_meses_processados += 1
                    await self.execution_repo.session.commit()

            except Exception as e:
                logger.error(f"Falha crítica ao orquestrar mês {mes}/{ano}: {str(e)}")
                stats["error"] += 1
                continue

        # 3. Finalizar e consolidar status
        finished_at = datetime.now(timezone.utc)
        annual_exec.finished_at = finished_at

        duration = finished_at - annual_exec.started_at
        annual_exec.duration_ms = int(duration.total_seconds() * 1000)

        if stats["success"] == 12:
            annual_exec.status = "success"
        elif stats["success"] > 0:
            annual_exec.status = "partial_success"
        else:
            annual_exec.status = "error"

        await self.execution_repo.session.commit()

        # Garantir que o objeto está na sessão (caso tenha sido expunged por engano)
        if annual_exec not in self.execution_repo.session:
            self.execution_repo.session.add(annual_exec)

        await self.execution_repo.session.refresh(annual_exec)

        logger.info(f"Orquestração anual {ano} finalizada. Status: {annual_exec.status}")

        return annual_exec

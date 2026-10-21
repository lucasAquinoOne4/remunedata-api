"""
app/persistence/repositories.py
Implementação do padrão Repository para abstrair o SQLAlchemy.
"""

from typing import Optional

from sqlalchemy import case, delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.persistence.models import (
    ExecutionAnnual,
    ExecutionMonthly,
    FederalRemuneracao,
    RemunerationCollected,
    SenadoRemuneracao,
)


def _like_pattern(term: str) -> str:
    """Padrão ILIKE '%termo%' sem acentos e com %, _ e \\ do usuário escapados (busca literal)."""
    plain = _strip_accents(term)
    for ch in ("\\", "%", "_"):
        plain = plain.replace(ch, "\\" + ch)
    return f"%{plain}%"


def _strip_accents(text: str) -> str:
    """Remove acentos do termo buscado (o índice é sobre o nome sem acento)."""
    import unicodedata

    return "".join(
        ch for ch in unicodedata.normalize("NFKD", text) if not unicodedata.combining(ch)
    )


# Contagem de resultados limitada: contar milhões de linhas para um nome comum custa segundos.
# Os repositórios devolvem no máximo COUNT_CAP + 1; o endpoint transforma em 'total_capped'.
COUNT_CAP = 10_000

# Quantas linhas candidatas a busca ordena em memória. Ordenar TODOS os resultados de um nome
# comum ("silva" tem centenas de milhares) custa segundos; ordena-se só as primeiras candidatas
# (a contagem já vira "10.000+" e o usuário refina a busca).
CANDIDATE_CAP = 3000


async def _ordered_page(session, model, name_col, ano_col, mes_col, where, limit, offset):
    """Página ordenada por nome, restrita às primeiras CANDIDATE_CAP linhas que casam."""
    base = select(model.id.label("id"), name_col.label("n"), ano_col.label("a"), mes_col.label("m"))
    base = base.where(*where) if isinstance(where, (list, tuple)) else base.where(where)
    cand = base.limit(CANDIDATE_CAP).subquery()
    ids = (
        (
            await session.execute(
                select(cand.c.id)
                .order_by(cand.c.n, cand.c.a.desc(), cand.c.m.desc())
                .limit(limit)
                .offset(offset)
            )
        )
        .scalars()
        .all()
    )
    if not ids:
        return []
    found = {
        r.id: r for r in (await session.execute(select(model).where(model.id.in_(ids)))).scalars()
    }
    return [found[i] for i in ids if i in found]


_INSERT_CHUNK_ROWS = 500  # 500 x 36 colunas = 18 mil parâmetros (< 32767)


class ExecutionRepository:
    """Repositório para gerenciar registros de execução (Anual e Mensal)."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_or_create_annual(self, ano: int) -> ExecutionAnnual:
        """Busca uma execução anual para o ano informado ou cria uma nova."""
        stmt = (
            select(ExecutionAnnual)
            .where(ExecutionAnnual.ano_exercicio == ano)
            .order_by(ExecutionAnnual.started_at.desc())
        )
        result = await self.session.execute(stmt)
        record = result.scalars().first()

        if not record:
            record = ExecutionAnnual(ano_exercicio=ano, status="pending")
            self.session.add(record)
            await self.session.commit()
            await self.session.refresh(record)

        return record

    async def get_monthly_execution_by_mes(
        self, annual_id: int, mes: str
    ) -> Optional[ExecutionMonthly]:
        """Busca um registro de execução mensal específico."""
        stmt = select(ExecutionMonthly).where(
            ExecutionMonthly.execution_id == annual_id, ExecutionMonthly.mes_referencia == mes
        )
        result = await self.session.execute(stmt)
        return result.scalars().first()

    async def create_monthly(self, annual_id: int, mes: str) -> ExecutionMonthly:
        """Cria ou recupera um registro de execução mensal."""
        existing = await self.get_monthly_execution_by_mes(annual_id, mes)
        if existing:
            # Se já existe, reiniciamos o status para running
            existing.status = "running"
            await self.session.commit()
            return existing

        monthly = ExecutionMonthly(execution_id=annual_id, mes_referencia=mes, status="running")
        self.session.add(monthly)
        await self.session.commit()
        await self.session.refresh(monthly)
        return monthly

    async def update_annual_progress(self, annual_id: int, pages: int, elements: int):
        """Atualiza os contadores globais da execução anual atomica e incrementalmente."""
        stmt = (
            update(ExecutionAnnual)
            .where(ExecutionAnnual.id == annual_id)
            .values(
                total_paginas_consumidas=ExecutionAnnual.total_paginas_consumidas + pages,
                total_registros_coletados=ExecutionAnnual.total_registros_coletados + elements,
            )
        )
        await self.session.execute(stmt)
        # Commit será feito pelo caller ou conforme estratégia de transação

    async def list_annual(self, limit: int = 20, offset: int = 0) -> list[ExecutionAnnual]:
        """Lista as execuções anuais paginadas."""
        stmt = (
            select(ExecutionAnnual)
            .order_by(ExecutionAnnual.started_at.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_annual(self, annual_id: int) -> Optional[ExecutionAnnual]:
        """Busca detalhe de uma execução anual acompanhada dos meses."""
        # Força carregamento dos meses se necessário ou usa joinedload
        from sqlalchemy.orm import selectinload

        stmt = (
            select(ExecutionAnnual)
            .where(ExecutionAnnual.id == annual_id)
            .options(selectinload(ExecutionAnnual.monthly_executions))
        )
        result = await self.session.execute(stmt)
        return result.scalars().first()

    async def sync_annual_stats(self, annual_id: int):
        """Re-calcula e sincroniza os totais da execução anual baseados nos meses de sucesso."""
        from sqlalchemy import func

        stmt = select(
            func.sum(ExecutionMonthly.paginas_consumidas),
            func.sum(ExecutionMonthly.registros_coletados),
            func.count(ExecutionMonthly.id),
        ).where(ExecutionMonthly.execution_id == annual_id, ExecutionMonthly.status == "success")
        stats_res = await self.session.execute(stmt)
        p_sums, r_sums, m_count = stats_res.fetchone() or (0, 0, 0)

        # Atualiza o registro anual
        annual = await self.get_annual(annual_id)
        if annual:
            annual.total_paginas_consumidas = p_sums or 0
            annual.total_registros_coletados = r_sums or 0
            annual.total_meses_processados = m_count or 0

            # Se todos os 12 meses estão success, marca o anual como success
            if m_count == 12:
                annual.status = "success"
            elif m_count > 0:
                annual.status = "partial_success"

            await self.session.commit()

    async def mark_interrupted(self) -> int:
        """
        Marca como interrompidas as execuções que ficaram 'running' (worker parado no meio,
        por exemplo no desligamento diário da instância). Mês -> 'error' (a coleta retoma de
        paginas_consumidas); ano -> 'partial_success' se já concluiu algum mês, senão 'error'.
        Retorna quantas execuções foram atualizadas.
        """
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        monthly = await self.session.execute(
            update(ExecutionMonthly)
            .where(ExecutionMonthly.status == "running")
            .values(
                status="error",
                error_message="Interrompida (reinício do worker); será retomada",
                finished_at=now,
            )
        )
        annual = await self.session.execute(
            update(ExecutionAnnual)
            .where(ExecutionAnnual.status == "running")
            .values(
                status=case(
                    (ExecutionAnnual.total_meses_processados > 0, "partial_success"),
                    else_="error",
                ),
                finished_at=now,
            )
        )
        await self.session.commit()
        return (monthly.rowcount or 0) + (annual.rowcount or 0)


class RemunerationRepository:
    """Repositório para gerenciar os dados de remuneração coletados."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def search(
        self,
        nome: Optional[str] = None,
        cpf: Optional[str] = None,
        ano: Optional[int] = None,
        mes: Optional[str] = None,
        cargo: Optional[str] = None,
        orgao: Optional[str] = None,
        limit: int = 25,
        offset: int = 0,
    ) -> tuple[list[RemunerationCollected], int]:
        """Busca paginada de registros com filtros."""
        from sqlalchemy import func

        stmt = select(RemunerationCollected)
        count_stmt = select(func.count()).select_from(RemunerationCollected)

        if nome:
            # índice trigram sobre immutable_unaccent(nome_servidor) (migration f7a1c3d9e5b2)
            cond = func.immutable_unaccent(RemunerationCollected.nome_servidor).ilike(
                _like_pattern(nome), escape="\\"
            )
            stmt = stmt.where(cond)
            count_stmt = count_stmt.where(cond)
        if cpf:
            stmt = stmt.where(RemunerationCollected.cpf_servidor == cpf)
            count_stmt = count_stmt.where(RemunerationCollected.cpf_servidor == cpf)
        if ano:
            stmt = stmt.where(RemunerationCollected.ano_exercicio == ano)
            count_stmt = count_stmt.where(RemunerationCollected.ano_exercicio == ano)
        if mes:
            stmt = stmt.where(RemunerationCollected.mes_referencia == mes)
            count_stmt = count_stmt.where(RemunerationCollected.mes_referencia == mes)
        if cargo:
            stmt = stmt.where(
                func.unaccent(RemunerationCollected.cargo).ilike(func.unaccent(f"%{cargo}%"))
            )
            count_stmt = count_stmt.where(
                func.unaccent(RemunerationCollected.cargo).ilike(func.unaccent(f"%{cargo}%"))
            )
        if orgao:
            stmt = stmt.where(
                func.unaccent(RemunerationCollected.nome_orgao).ilike(func.unaccent(f"%{orgao}%"))
            )
            count_stmt = count_stmt.where(
                func.unaccent(RemunerationCollected.nome_orgao).ilike(func.unaccent(f"%{orgao}%"))
            )

        # Count total (limitado: ver COUNT_CAP)
        capped = stmt.with_only_columns(RemunerationCollected.id).limit(COUNT_CAP + 1).subquery()
        total = (await self.session.execute(select(func.count()).select_from(capped))).scalar_one()

        # Página ordenada por nome (candidatas limitadas: ver CANDIDATE_CAP)
        where = list(stmt._where_criteria)
        rows = await _ordered_page(
            self.session,
            RemunerationCollected,
            RemunerationCollected.nome_servidor,
            RemunerationCollected.ano_exercicio,
            RemunerationCollected.mes_referencia,
            where,
            limit,
            offset,
        )
        return rows, total

    async def get_distinct_filters(self) -> dict[str, list[str]]:
        """Busca valores únicos de cargo e órgão para preencher selects no frontend."""
        stmt_cargos = (
            select(RemunerationCollected.cargo)
            .where(RemunerationCollected.cargo.isnot(None))
            .distinct()
            .order_by(RemunerationCollected.cargo)
        )
        stmt_orgaos = (
            select(RemunerationCollected.nome_orgao)
            .where(RemunerationCollected.nome_orgao.isnot(None))
            .distinct()
            .order_by(RemunerationCollected.nome_orgao)
        )

        res_cargos = await self.session.execute(stmt_cargos)
        res_orgaos = await self.session.execute(stmt_orgaos)

        return {
            "cargos": [str(c) for c in res_cargos.scalars().all() if c],
            "orgaos": [str(o) for o in res_orgaos.scalars().all() if o],
        }

    async def save_batch(self, items: list[RemunerationCollected]):
        """
        Salva uma lista de registros em lote, ignorando duplicatas.

        Usa INSERT ... ON CONFLICT DO NOTHING contra o índice único
        (ano, mês, md5(payload)), então coletas repetidas ou concorrentes não duplicam dados.
        """
        if not items:
            return

        from datetime import datetime, timezone

        from sqlalchemy.dialects.postgresql import insert as pg_insert

        columns = RemunerationCollected.__table__.columns
        rows = []
        for obj in items:
            row = {}
            for col in columns:
                if col.name == "id":
                    continue
                value = getattr(obj, col.name, None)
                if value is None and col.default is not None:
                    arg = col.default.arg
                    value = arg(None) if callable(arg) else arg
                if value is None and col.name == "created_at":
                    value = datetime.now(timezone.utc)
                row[col.name] = value
            rows.append(row)

        # asyncpg aceita no máx. 32767 parâmetros por comando (36 colunas x linhas): fatia o INSERT
        for start in range(0, len(rows), _INSERT_CHUNK_ROWS):
            chunk = rows[start : start + _INSERT_CHUNK_ROWS]
            await self.session.execute(
                pg_insert(RemunerationCollected).values(chunk).on_conflict_do_nothing()
            )
        await self.session.commit()

    async def get_all_by_execution(
        self, execution_id: int, limit: Optional[int] = None
    ) -> list[RemunerationCollected]:
        """Recupera registros de uma execução com ordenação e limite opcional."""
        stmt = (
            select(RemunerationCollected)
            .where(RemunerationCollected.execution_id == execution_id)
            .order_by(RemunerationCollected.mes_referencia, RemunerationCollected.nome_servidor)
        )
        if limit:
            stmt = stmt.limit(limit)

        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_summary(self, ano: Optional[int] = None) -> dict:
        """Calcula agregados para o dashboard (count(*) permite index-only scan, ver ix_remuneration_summary)."""
        from sqlalchemy import func

        # Filtro opcional por ano
        filter_stmt = []
        if ano:
            filter_stmt.append(RemunerationCollected.ano_exercicio == ano)

        # 1. Totais Gerais
        stmt_totals = select(
            func.count().label("total_count"),
            func.avg(RemunerationCollected.valor_bruto).label("avg_bruto"),
            func.sum(RemunerationCollected.valor_bruto).label("sum_bruto"),
        )
        if filter_stmt:
            stmt_totals = stmt_totals.where(*filter_stmt)

        res_totals = await self.session.execute(stmt_totals)
        totals = res_totals.mappings().one()

        # 2. Top 5 Órgãos por quantidade
        stmt_orgaos = (
            select(
                RemunerationCollected.nome_orgao,
                func.count().label("count"),
            )
            .group_by(RemunerationCollected.nome_orgao)
            .order_by(func.count().desc())
            .limit(5)
        )
        if filter_stmt:
            stmt_orgaos = stmt_orgaos.where(*filter_stmt)

        res_orgaos = await self.session.execute(stmt_orgaos)
        top_orgaos = [dict(row._mapping) for row in res_orgaos]

        return {
            "total_servidores": totals["total_count"],
            "media_salarial": float(totals["avg_bruto"] or 0),
            "total_gasto_bruto": float(totals["sum_bruto"] or 0),
            "top_orgaos": top_orgaos,
        }

    async def delete_monthly_records(self, monthly_id: int):
        """Remove todos os registros de remuneração de uma execução mensal específica. (Obsoleto: use delete_records_by_period)"""
        from sqlalchemy import delete

        stmt = delete(RemunerationCollected).where(
            RemunerationCollected.monthly_execution_id == monthly_id
        )
        await self.session.execute(stmt)
        await self.session.commit()

    async def delete_records_by_period(self, ano: int, mes: str):
        """Remove todos os registros de remuneração para um ano/mês específico globalmente."""
        from sqlalchemy import delete

        stmt = delete(RemunerationCollected).where(
            RemunerationCollected.ano_exercicio == ano, RemunerationCollected.mes_referencia == mes
        )
        await self.session.execute(stmt)
        await self.session.commit()


class SenadoRepository:
    """Repositório da remuneração de servidores do Senado Federal."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def replace_month(self, ano: int, mes: int, records: list[SenadoRemuneracao]) -> None:
        """Substitui todos os registros do mês (idempotente) em uma única transação."""
        await self.session.execute(
            delete(SenadoRemuneracao).where(
                SenadoRemuneracao.ano == ano, SenadoRemuneracao.mes == mes
            )
        )
        self.session.add_all(records)
        await self.session.commit()

    async def search(
        self,
        nome: Optional[str] = None,
        ano: Optional[int] = None,
        mes: Optional[str] = None,
        cargo: Optional[str] = None,
        orgao: Optional[str] = None,
        limit: int = 25,
        offset: int = 0,
    ) -> tuple[list[dict], int]:
        """Busca paginada, devolvendo itens no mesmo formato do RemunerationRead."""
        filters = []
        if nome:
            filters.append(
                func.immutable_unaccent(SenadoRemuneracao.nome).ilike(
                    _like_pattern(nome), escape="\\"
                )
            )
        if ano:
            filters.append(SenadoRemuneracao.ano == ano)
        if mes:
            filters.append(SenadoRemuneracao.mes == int(mes))
        if cargo:
            filters.append(
                func.unaccent(SenadoRemuneracao.cargo).ilike(func.unaccent(f"%{cargo}%"))
            )
        if orgao:
            filters.append(
                func.unaccent(SenadoRemuneracao.lotacao_nome).ilike(func.unaccent(f"%{orgao}%"))
            )

        capped = select(SenadoRemuneracao.id).where(*filters).limit(COUNT_CAP + 1).subquery()
        total = (await self.session.execute(select(func.count()).select_from(capped))).scalar_one()

        rows = await _ordered_page(
            self.session,
            SenadoRemuneracao,
            SenadoRemuneracao.nome,
            SenadoRemuneracao.ano,
            SenadoRemuneracao.mes,
            filters,
            limit,
            offset,
        )
        return [self.to_read(r) for r in rows], total

    async def get_distinct_filters(self) -> dict[str, list[str]]:
        """Cargos e lotações únicos, para os selects do frontend."""
        res_cargos = await self.session.execute(
            select(SenadoRemuneracao.cargo)
            .where(SenadoRemuneracao.cargo.isnot(None))
            .distinct()
            .order_by(SenadoRemuneracao.cargo)
        )
        res_lot = await self.session.execute(
            select(SenadoRemuneracao.lotacao_nome)
            .where(SenadoRemuneracao.lotacao_nome.isnot(None))
            .distinct()
            .order_by(SenadoRemuneracao.lotacao_nome)
        )
        return {
            "cargos": [str(c) for c in res_cargos.scalars().all() if c],
            "orgaos": [str(o) for o in res_lot.scalars().all() if o],
        }

    @staticmethod
    def to_read(r: SenadoRemuneracao) -> dict:
        """Mapeia para o formato comum de RemunerationRead."""
        return {
            "id": r.id,
            "fonte": "SENADO",
            "ano_exercicio": r.ano,
            "mes_referencia": f"{r.mes:02d}",
            "codigo_identificacao": str(r.sequencial),
            "codigo_matricula": str(r.sequencial),
            "nome_servidor": r.nome,
            "cpf_servidor": None,
            "codigo_orgao": r.lotacao_sigla,
            "nome_orgao": r.lotacao_nome,
            "cargo": r.cargo,
            "valor_bruto": r.valor_bruto,
            "valor_liquido": r.valor_liquido,
            "created_at": r.created_at,
        }


class FederalRepository:
    """Repositório da remuneração de servidores civis do Executivo federal."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def month_loaded(self, ano: int, mes: int) -> bool:
        """True se o mês já tem registros (a verificação semanal pula sem bater no portal)."""
        found = await self.session.execute(
            select(FederalRemuneracao.id)
            .where(FederalRemuneracao.ano == ano, FederalRemuneracao.mes == mes)
            .limit(1)
        )
        return found.first() is not None

    async def replace_month(self, ano: int, mes: int, rows, chunk: int = 1000) -> int:
        """Apaga o mês e insere `rows` (iterável de dicts) em lotes, numa única transação."""
        await self.session.execute(
            delete(FederalRemuneracao).where(
                FederalRemuneracao.ano == ano, FederalRemuneracao.mes == mes
            )
        )
        total, batch = 0, []
        for row in rows:
            batch.append(row)
            if len(batch) >= chunk:
                await self.session.execute(FederalRemuneracao.__table__.insert(), batch)
                total += len(batch)
                batch = []
        if batch:
            await self.session.execute(FederalRemuneracao.__table__.insert(), batch)
            total += len(batch)
        await self.session.commit()
        return total

    async def search(
        self,
        nome: Optional[str] = None,
        ano: Optional[int] = None,
        mes: Optional[str] = None,
        cargo: Optional[str] = None,
        orgao: Optional[str] = None,
        limit: int = 25,
        offset: int = 0,
    ) -> tuple[list[dict], int]:
        """Busca paginada no formato comum de RemunerationRead."""
        filters = []
        if nome:
            filters.append(
                func.immutable_unaccent(FederalRemuneracao.nome).ilike(
                    _like_pattern(nome), escape="\\"
                )
            )
        if ano:
            filters.append(FederalRemuneracao.ano == ano)
        if mes:
            filters.append(FederalRemuneracao.mes == int(mes))
        if cargo:
            filters.append(FederalRemuneracao.cargo == cargo)
        if orgao:
            filters.append(FederalRemuneracao.org_exercicio == orgao)

        capped = select(FederalRemuneracao.id).where(*filters).limit(COUNT_CAP + 1).subquery()
        total = (await self.session.execute(select(func.count()).select_from(capped))).scalar_one()
        rows = await _ordered_page(
            self.session,
            FederalRemuneracao,
            FederalRemuneracao.nome,
            FederalRemuneracao.ano,
            FederalRemuneracao.mes,
            filters,
            limit,
            offset,
        )
        return [self.to_read(r) for r in rows], total

    async def get_distinct_filters(self) -> dict[str, list[str]]:
        """Cargos e órgãos de exercício únicos (do mês mais recente carregado)."""
        latest = (
            await self.session.execute(
                select(FederalRemuneracao.ano, FederalRemuneracao.mes)
                .order_by(FederalRemuneracao.ano.desc(), FederalRemuneracao.mes.desc())
                .limit(1)
            )
        ).first()
        if latest is None:
            return {"cargos": [], "orgaos": []}
        scope = (FederalRemuneracao.ano == latest[0], FederalRemuneracao.mes == latest[1])
        cargos = await self.session.execute(
            select(FederalRemuneracao.cargo)
            .where(*scope, FederalRemuneracao.cargo.isnot(None))
            .distinct()
            .order_by(FederalRemuneracao.cargo)
        )
        orgaos = await self.session.execute(
            select(FederalRemuneracao.org_exercicio)
            .where(*scope, FederalRemuneracao.org_exercicio.isnot(None))
            .distinct()
            .order_by(FederalRemuneracao.org_exercicio)
        )
        return {
            "cargos": [str(c) for c in cargos.scalars().all() if c],
            "orgaos": [str(o) for o in orgaos.scalars().all() if o],
        }

    @staticmethod
    def to_read(r: FederalRemuneracao) -> dict:
        return {
            "id": r.id,
            "fonte": "FEDERAL",
            "ano_exercicio": r.ano,
            "mes_referencia": f"{r.mes:02d}",
            "codigo_identificacao": str(r.id_servidor_portal),
            "codigo_matricula": r.matricula_mascarada or str(r.id_servidor_portal),
            "nome_servidor": r.nome,
            "cpf_servidor": r.cpf_mascarado,
            "codigo_orgao": None,
            "nome_orgao": r.org_exercicio or r.org_lotacao,
            "cargo": r.cargo,
            "valor_bruto": r.valor_bruto,
            "valor_liquido": r.valor_liquido,
            "created_at": None,
        }

"""
app/collector/monthly.py
Coleta dados de um mês específico e persiste no banco via Repositórios.
"""

import asyncio
import json
from datetime import datetime, timezone

from app.core.config import settings
from app.core.logging import get_logger
from app.infra.transparencia_client import TransparenciaClient
from app.persistence.models import ExecutionMonthly, RemunerationCollected
from app.persistence.repositories import ExecutionRepository, RemunerationRepository

logger = get_logger(__name__)


class MonthlyCollector:
    """
    Coleta dados de um mês específico e persiste no banco via Repositórios.
    """

    def __init__(
        self,
        client: TransparenciaClient,
        execution_repo: ExecutionRepository,
        remuneration_repo: RemunerationRepository,
    ):
        self.client = client
        self.execution_repo = execution_repo
        self.remuneration_repo = remuneration_repo

    async def collect(self, ano: int, mes: str, annual_execution_id: int) -> ExecutionMonthly:
        """
        Executa a coleta para um mês/ano específico.
        """
        logger.info(f"Iniciando coleta: {mes}/{ano}")

        # 1. Criar registro mensal via Repo
        monthly_exec = await self.execution_repo.create_monthly(annual_execution_id, mes)

        # 1.1 Limpar dados parciais APENAS se estivermos começando do zero (Fresh Start)
        # Se paginas_consumidas > 0, pulamos a limpeza e retomamos de onde paramos (Super Nitro)
        if monthly_exec.paginas_consumidas == 0:
            logger.info(
                f"Limpando registros globais para {mes}/{ano} antes de iniciar nova coleta."
            )
            await self.remuneration_repo.delete_records_by_period(ano, mes)
        else:
            logger.info(
                f"Super Nitro: Retomando coleta para {mes}/{ano} a partir da pág {monthly_exec.paginas_consumidas}."
            )

        page = monthly_exec.paginas_consumidas
        batch_size = settings.transparencia_batch_size
        size = settings.transparencia_page_size
        stop_collection = False

        try:
            while not stop_collection:
                # 2. Coleta em lote (Batch de 5 páginas simultâneas)
                batch_pages = list(range(page, page + batch_size))
                logger.debug(f"Coletando lote de páginas: {batch_pages} para {mes}/{ano}")

                batch_requests = []
                for p in batch_pages:

                    async def get_with_retry(page_to_get=p):
                        max_retries = 3
                        backoff = 1
                        for attempt in range(max_retries):
                            try:
                                return await self.client.get_remuneracao(
                                    ano=ano, mes=mes, page=page_to_get, size=size
                                )
                            except Exception as e:
                                if attempt == max_retries - 1:
                                    raise e
                                logger.warning(
                                    f"Tentativa {attempt + 1} falhou para pág {page_to_get} ({mes}/{ano}): {repr(e)}. "
                                    f"Retentando em {backoff}s..."
                                )
                                await asyncio.sleep(backoff)
                                backoff *= 2

                    batch_requests.append(get_with_retry(p))

                results = await asyncio.gather(*batch_requests, return_exceptions=True)

                all_records = []
                total_new_pages = 0

                for i, data in enumerate(results):
                    if isinstance(data, Exception):
                        error_type = type(data).__name__
                        error_msg = str(data) or repr(data)
                        logger.error(
                            f"Erro fatal após retentativas na página {batch_pages[i]} ({mes}/{ano}): [{error_type}] {error_msg}"
                        )
                        stop_collection = True
                        monthly_exec.status = "error"
                        monthly_exec.error_message = f"[{error_type}] {error_msg}"
                        break

                    content = data.get("content", [])
                    num_elements = data.get("numberOfElements", 0)
                    is_last = data.get("last", False)

                    if not content or num_elements == 0:
                        logger.info(
                            f"Fim da paginação em {mes}/{ano}: página {batch_pages[i]} vazia."
                        )
                        stop_collection = True
                        break

                    # Mapear registros desta página
                    page_records = [
                        RemunerationCollected(
                            execution_id=annual_execution_id,
                            monthly_execution_id=monthly_exec.id,
                            ano_exercicio=ano,
                            mes_referencia=mes,
                            codigo_identificacao=str(item.get("codigoIdentificacao", "")),
                            codigo_matricula=str(item.get("codigoMatricula", "")),
                            cpf_servidor=item.get("cpfServidor"),
                            nome_servidor=item.get("nomeServidor", "N/A"),
                            codigo_orgao=item.get("codigoOrgao"),
                            nome_orgao=item.get("nomeOrgao"),
                            situacao_funcional=item.get("situacaoFuncional"),
                            situacao_funcional_detalhada=item.get("situacaoFuncionalDetalhada"),
                            cargo=item.get("cargo"),
                            funcao=item.get("funcao"),
                            valor_remuneracao_basica=float(item.get("valorRemuneracaoBasica", 0)),
                            valor_beneficios=float(item.get("valorBeneficios", 0)),
                            valor_funcoes=float(item.get("valorFuncoes", 0)),
                            valor_comissao_conselheiro=float(
                                item.get("valorComissaoConselheiro", 0)
                            ),
                            valor_hora_extra=float(item.get("valorHoraExtra", 0)),
                            valor_verbas_eventuais=float(item.get("valorVerbasEventuais", 0)),
                            valor_verbas_judiciais=float(item.get("valorVerbasJudiciais", 0)),
                            valor_receitas_meses_anteriores=float(
                                item.get("valorReceitasMesesAnteriores", 0)
                            ),
                            valor_reposicao_descontos_maior=float(
                                item.get("valorReposicaoDescontosMaior", 0)
                            ),
                            valor_licenca_premio=float(item.get("valorLicencaPremio", 0)),
                            valor_imposto_renda=float(item.get("valorImpostoRenda", 0)),
                            valor_seguridade_social=float(item.get("valorSeguridadeSocial", 0)),
                            valor_redutor_teto=float(item.get("valorRedutorTeto", 0)),
                            valor_descontos_meses_anteriores=float(
                                item.get("valorDescontosMesesAnteriores", 0)
                            ),
                            valor_reposicao_pagamento_maior=float(
                                item.get("valorReposicaoPagamentoMaior", 0)
                            ),
                            valor_expurgo=float(item.get("valorExpurgo", 0)),
                            valor_liquido=float(item.get("valorLiquido", 0)),
                            valor_bruto=float(item.get("valorBruto", 0)),
                            raw_payload_json=json.dumps(item),
                        )
                        for item in content
                    ]
                    all_records.extend(page_records)
                    total_new_pages += 1

                    if is_last:
                        logger.info(
                            f"Fim da paginação em {mes}/{ano}: marca 'last=true' na pág {batch_pages[i]}."
                        )
                        stop_collection = True
                        break

                # 3. Salvar lote acumulado de todas as páginas do batch
                if all_records:
                    await self.remuneration_repo.save_batch(all_records)

                    # Atualizar contadores
                    monthly_exec.paginas_consumidas += total_new_pages
                    monthly_exec.registros_coletados += len(all_records)

                    await self.execution_repo.update_annual_progress(
                        annual_execution_id, pages=total_new_pages, elements=len(all_records)
                    )

                    # Commit e Expunge APENAS dos registros coletados para liberar memória
                    # Mantemos os objetos de execução (monthly_exec/annual_exec) na sessão
                    await self.execution_repo.session.commit()
                    for record in all_records:
                        try:
                            self.execution_repo.session.expunge(record)
                        except Exception:
                            pass

                # Incrementar ponteiro de página para o próximo batch
                page += batch_size
                if settings.transparencia_request_delay_seconds:
                    await asyncio.sleep(settings.transparencia_request_delay_seconds)

            if monthly_exec.status != "error":
                monthly_exec.status = "success"
            monthly_exec.finished_at = datetime.now(timezone.utc)

        except Exception as e:
            logger.error(f"Erro fatal na coleta de {mes}/{ano}: {str(e)}")
            monthly_exec.status = "error"
            monthly_exec.error_message = str(e)
            monthly_exec.finished_at = datetime.now(timezone.utc)

        await self.execution_repo.session.commit()
        return monthly_exec

"""
app/core/config.py
Configuração central da aplicação via Pydantic BaseSettings.
Lê variáveis de ambiente (ou arquivo .env) e valida os valores.
"""

import json
from typing import Any, Optional, Union

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Configurações da aplicação lidas de variáveis de ambiente.

    As variáveis devem ser definidas no arquivo .env (não commitado) ou
    diretamente no ambiente. O arquivo .env.example serve como referência.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        # Ignora variáveis extras no .env (não causa erro)
        extra="ignore",
    )

    # Banco de dados
    database_url: str = Field(
        description="URL de conexão assíncrona (asyncpg) para SQLAlchemy 2.x",
    )
    database_url_sync: str = Field(
        description="URL de conexão síncrona (psycopg2) para Alembic migrations",
    )

    # Supabase Auth
    supabase_url: str = Field(
        description="URL do projeto Supabase",
    )
    supabase_jwt_secret: str = Field(
        description="Segredo JWT para validação de tokens (HS256)",
    )
    supabase_jwks_url: Optional[str] = Field(
        default=None,
        description="URL para chaves ES256 (ECC) do Supabase, se usado para validação de tokens",
    )

    # Sentry
    sentry_dsn: Optional[str] = Field(
        default=None,
        description="DSN do projeto Sentry para rastreamento de erros",
    )

    # Redis
    redis_url: str = Field(
        default="redis://localhost:6379/0",
        description="URL de conexão com Redis",
    )

    # Aplicação
    app_env: str = Field(
        default="development",
        description="Ambiente da aplicação: development, staging, production",
    )
    log_level: str = Field(
        default="INFO",
        description="Nível de logging: DEBUG, INFO, WARNING, ERROR, CRITICAL",
    )

    # API do Portal da Transparência
    transparencia_api_base_url: str = Field(
        default="https://www.transparencia.df.gov.br/api",
        description="URL base da API do Portal da Transparência do DF",
    )
    transparencia_page_size: int = Field(
        default=150,
        description="Tamanho máximo de página validado na API (não alterar sem nova validação)",
        ge=1,
        le=150,
    )
    transparencia_client_id: str = Field(
        default="remunedata",
        description="Valor do header x-client-id exigido pela API do Portal da Transparência",
    )
    senado_api_base_url: str = Field(
        default="https://adm.senado.gov.br/adm-dadosabertos/api/v1",
        description="URL base da API de dados abertos administrativos do Senado Federal",
    )
    senado_timeout_seconds: int = Field(
        default=120,
        description="Timeout em segundos para a API do Senado (cada mês vem numa só resposta grande)",
        ge=5,
        le=600,
    )
    senado_sync_months: int = Field(
        default=3,
        description="Quantos meses recentes a sincronização diária do Senado revisita",
        ge=1,
        le=24,
    )
    transparencia_recent_months_first: bool = Field(
        default=True,
        description="Coleta os meses do mais recente para o mais antigo (dados novos aparecem antes)",
    )
    portal_federal_download_base_url: str = Field(
        default="https://portaldatransparencia.gov.br/download-de-dados/servidores",
        description="Base dos downloads mensais (ZIP) de servidores do Portal da Transparência federal",
    )
    portal_federal_api_key: str = Field(
        default="",
        description=(
            "Chave da API do Portal da Transparência federal (header chave-api-dados). "
            "A carga em lote por ZIP não usa chave; reservada para enriquecer detalhes via API."
        ),
    )
    federal_sync_months: int = Field(
        default=2,
        description="Quantos meses recentes a verificação semanal do Executivo federal considera",
        ge=1,
        le=12,
    )
    transparencia_batch_size: int = Field(
        default=1,
        description="Páginas buscadas em paralelo por lote (reduzir se a API responder 429)",
        ge=1,
        le=10,
    )
    transparencia_request_delay_seconds: float = Field(
        default=2.0,
        description="Pausa entre lotes de páginas, para evitar 429 da API",
        ge=0,
        le=30,
    )
    transparencia_timeout_seconds: int = Field(
        default=30,
        description="Timeout em segundos para requisições HTTP à API",
        ge=5,
        le=120,
    )

    # CORS
    cors_origins: Union[str, list[str]] = Field(
        default=["*"],
        description="Lista de origens permitidas para CORS (ex: ['https://remunedata.com.br'])",
    )

    # AbacatePay
    abacatepay_api_key: str = Field(
        default="",
        description="Bearer token da conta AbacatePay",
    )
    abacatepay_webhook_secret: str = Field(
        default="",
        description="Secret para validação dos webhooks da AbacatePay",
    )
    abacatepay_methods: str = Field(
        default="PIX,CARD",
        description=(
            "Meios de pagamento do checkout, separados por vírgula (PIX, CARD, BOLETO). "
            "Meios não habilitados na loja são descartados automaticamente."
        ),
    )
    abacatepay_public_key: str = Field(
        default="",
        description=(
            "Chave pública de assinatura de webhooks (publicada na documentação da AbacatePay). "
            "Se definida, a assinatura X-Webhook-Signature (HMAC-SHA256, base64) é validada."
        ),
    )
    abacatepay_base_url: str = Field(
        default="https://api.abacatepay.com/v2",
        description="URL base da API AbacatePay (v2)",
    )
    front_url: str = Field(
        default="https://remunedata.com.br",
        description="URL base do frontend (usada nos redirects de pagamento)",
    )

    # Configurações Admin
    supabase_service_role_key: str = Field(
        default="",
        description="Chave secreta para operações administrativas no Supabase",
    )
    admin_path_prefix: str = Field(
        default="admin-secret-dashboard",
        description="Prefixo da URL para o painel administrativo",
    )
    admin_emails: Union[str, list[str]] = Field(
        default=[],
        description="Lista de e-mails com permissão de administrador",
    )

    @field_validator("cors_origins", "admin_emails", mode="before")
    @classmethod
    def assemble_list_from_string(cls, v: Any) -> list[str]:
        """
        Permite que listas sejam passadas como strings separadas por vírgula em ENVs.
        Se receber uma string, converte para lista. Se já for lista, mantém.
        """
        if isinstance(v, str):
            if v.startswith("[") and v.endswith("]"):
                try:
                    return json.loads(v)
                except (json.JSONDecodeError, TypeError, ValueError):
                    pass
            return [i.strip() for i in v.split(",") if i.strip()]
        return v

    @property
    def is_development(self) -> bool:
        return self.app_env == "development"

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"


def get_settings() -> Settings:
    """
    Retorna instância das configurações.
    """
    return Settings()


# Instância global para uso no app
settings = get_settings()

"""Application configuration.

Every runtime knob lives here so that the product can be re-branded, re-priced
and re-hosted without touching business logic. Configuration is validated at
import time; in production a misconfiguration aborts startup with an explicit,
human readable message instead of failing later inside a worker.
"""

from __future__ import annotations

import functools
import secrets
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import ValidationError, computed_field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

_PARENTS = Path(__file__).resolve().parents

#: In a source checkout this file sits at ``apps/api/picglot/core/config.py``,
#: so the API package root is two levels up and the repository root four.
#: A deployed image copies the package to ``/app/picglot/core/``, where no
#: fourth ancestor exists — indexing it blindly raised IndexError on import and
#: took down every container. There, both roots collapse to the app directory,
#: which is what the two callers below actually want anyway.
API_ROOT = _PARENTS[2] if len(_PARENTS) > 2 else _PARENTS[-1]
REPO_ROOT = _PARENTS[4] if len(_PARENTS) > 4 else API_ROOT

Environment = Literal["development", "staging", "production", "test"]

DEV_SECRET_KEY = "dev-insecure-secret-key-change-me-in-production-0000000000000000"


def _csv(value: Any) -> list[str]:
    """Accept both a comma separated string and a real list."""
    if value is None or value == "":
        return []
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    if isinstance(value, (list, tuple)):
        return [str(item).strip() for item in value if str(item).strip()]
    raise TypeError(f"cannot interpret {value!r} as a list")


#: A list configured as a comma separated string in the environment.
#: ``NoDecode`` stops pydantic-settings from trying to JSON-parse the value first,
#: so ``_split_csv`` below sees the raw string. (Pydantic deep-copies field
#: defaults, so a literal list default is safe.)
CsvList = Annotated[list[str], NoDecode]


class ConfigurationError(RuntimeError):
    """Raised when the process cannot safely start with the given settings."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(REPO_ROOT / ".env", API_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---------------------------------------------------------------- brand
    brand_name: str = "PicGlot"
    brand_legal_entity: str = "PicGlot"
    brand_support_email: str = "support@picglot.ru"
    brand_domain: str = "picglot.ru"
    public_web_url: str = "http://localhost:3000"
    public_api_url: str = "http://localhost:8000"

    # -------------------------------------------------------------- runtime
    environment: Environment = "development"
    debug: bool = True
    log_level: str = "INFO"
    log_format: Literal["console", "json"] = "console"
    maintenance_mode: bool = False
    secret_key: str = DEV_SECRET_KEY
    secret_key_previous: CsvList = []

    # ------------------------------------------------------------- database
    database_url: str = "postgresql+psycopg://picglot:picglot@localhost:5432/picglot"
    database_pool_size: int = 10
    database_max_overflow: int = 20
    database_echo: bool = False

    # ---------------------------------------------------------------- redis
    redis_url: str = "redis://localhost:6379/0"

    # ---------------------------------------------------------------- queue
    queue_backend: Literal["celery", "inline"] = "celery"
    queue_default: str = "cpu"
    queue_gpu_enabled: bool = False
    job_max_attempts: int = 3
    job_soft_time_limit_seconds: int = 900
    job_hard_time_limit_seconds: int = 1200
    job_stuck_after_seconds: int = 1800
    max_concurrent_jobs_per_user: int = 3
    max_concurrent_jobs_per_workspace: int = 10

    # -------------------------------------------------------------- storage
    storage_backend: Literal["s3", "local"] = "s3"
    s3_endpoint_url: str = "http://localhost:9000"
    s3_public_endpoint_url: str = ""
    s3_region: str = "us-east-1"
    s3_access_key_id: str = "picglot"
    s3_secret_access_key: str = "picglot-dev-secret"
    s3_bucket: str = "picglot"
    s3_force_path_style: bool = True
    s3_use_ssl: bool = False
    s3_server_side_encryption: str = ""
    signed_url_ttl_seconds: int = 900
    local_storage_path: str = "./var/storage"

    # --------------------------------------------------------- upload limits
    max_upload_bytes_guest: int = 10 * 1024 * 1024
    max_upload_bytes_free: int = 10 * 1024 * 1024
    max_upload_bytes_pro: int = 100 * 1024 * 1024
    max_upload_bytes_business: int = 200 * 1024 * 1024
    max_image_pixels: int = 80_000_000
    max_pdf_pages_guest: int = 3
    max_pdf_pages_free: int = 10
    max_pdf_pages_pro: int = 300
    max_pdf_pages_business: int = 1000
    max_batch_files_free: int = 5
    max_batch_files_pro: int = 50
    max_batch_files_business: int = 100
    allow_svg_upload: bool = False
    antivirus_enabled: bool = False
    clamav_host: str = "clamav"
    clamav_port: int = 3310

    # -------------------------------------------------------------- locales
    default_locale: str = "en"
    enabled_locales: CsvList = ["en", "ru", "es", "de", "fr", "pt", "tr", "id", "pl", "uk"]

    # --------------------------------------------------------------- OCR
    ocr_provider_priority: CsvList = ["rapidocr", "tesseract"]
    ocr_handwriting_provider_priority: CsvList = ["llm", "azure_vision", "tesseract"]
    ocr_table_provider_priority: CsvList = ["builtin", "azure_vision", "aws_textract"]
    ocr_timeout_seconds: int = 120
    ocr_max_retries: int = 2
    tesseract_cmd: str = ""
    tessdata_prefix: str = ""

    google_vision_credentials_json: str = ""
    google_vision_enabled: bool = False
    azure_vision_endpoint: str = ""
    azure_vision_key: str = ""
    azure_vision_enabled: bool = False
    aws_textract_region: str = "us-east-1"
    aws_access_key_id: str = ""
    aws_secret_access_key: str = ""
    aws_textract_enabled: bool = False
    yandex_vision_api_key: str = ""
    yandex_vision_folder_id: str = ""
    yandex_vision_enabled: bool = False

    # -------------------------------------------------------- translation
    translation_provider_priority: CsvList = ["argos", "deepl", "google_translate", "llm"]
    translation_timeout_seconds: int = 60
    translation_max_chars_per_request: int = 4000
    translation_cache_ttl_seconds: int = 604800

    deepl_api_key: str = ""
    deepl_api_url: str = "https://api-free.deepl.com/v2"
    google_translate_api_key: str = ""
    google_translate_project_id: str = ""
    yandex_translate_api_key: str = ""
    yandex_translate_folder_id: str = ""
    azure_translator_key: str = ""
    azure_translator_region: str = ""
    azure_translator_endpoint: str = "https://api.cognitive.microsofttranslator.com"

    # ------------------------------------------------------------------ LLM
    llm_provider: Literal["anthropic", "openai_compatible", "disabled"] = "anthropic"
    llm_model: str = "claude-sonnet-5"
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    llm_max_output_tokens: int = 4096
    llm_max_schema_retries: int = 2
    llm_daily_cost_limit_usd: float = 50.0

    # ---------------------------------------------------------- processing
    local_only_processing: bool = False
    inpaint_strategy: Literal["auto", "telea", "ns", "solid", "overlay"] = "auto"
    advanced_inpaint_enabled: bool = False
    preview_max_dimension: int = 1600
    thumbnail_max_dimension: int = 320
    processing_max_dimension: int = 4000
    font_dir: str = "./assets/fonts"

    # ----------------------------------------------------------- retention
    retention_guest_hours: int = 24
    retention_free_hours: int = 168
    retention_pro_hours: int = 2160
    retention_business_hours: int = 8760
    retention_intermediate_hours: int = 6
    retention_trash_hours: int = 720

    # ---------------------------------------------------------------- auth
    session_ttl_seconds: int = 2_592_000
    session_cookie_name: str = "picglot_session"
    session_cookie_secure: bool = False
    session_cookie_domain: str = ""
    guest_cookie_name: str = "picglot_guest"
    csrf_cookie_name: str = "picglot_csrf"
    password_min_length: int = 10
    argon2_time_cost: int = 3
    argon2_memory_cost: int = 65536
    argon2_parallelism: int = 4
    email_verification_ttl_seconds: int = 86400
    magic_link_ttl_seconds: int = 900
    password_reset_ttl_seconds: int = 3600
    totp_issuer: str = "PicGlot"
    google_oauth_client_id: str = ""
    google_oauth_client_secret: str = ""
    google_oauth_enabled: bool = False

    # -------------------------------------------------------- rate limiting
    rate_limit_enabled: bool = True
    rate_limit_anon_per_minute: int = 60
    rate_limit_user_per_minute: int = 180
    rate_limit_login_per_15min: int = 10
    rate_limit_upload_per_hour: int = 120
    rate_limit_api_per_minute: int = 120
    rate_limit_share_per_minute: int = 30

    # ------------------------------------------------------------- billing
    billing_enabled: bool = True
    billing_provider: Literal["stripe", "yookassa", "manual"] = "stripe"
    billing_test_mode: bool = True
    billing_currency: str = "USD"
    stripe_secret_key: str = ""
    stripe_publishable_key: str = ""
    stripe_webhook_secret: str = ""
    stripe_price_pro_monthly: str = ""
    stripe_price_business_monthly: str = ""
    yookassa_shop_id: str = ""
    yookassa_secret_key: str = ""
    yookassa_webhook_secret: str = ""

    # --------------------------------------------------------------- email
    email_provider: Literal["smtp", "resend", "console"] = "smtp"
    email_from: str = "noreply@picglot.ru"
    email_from_name: str = "PicGlot"
    smtp_host: str = "localhost"
    smtp_port: int = 1025
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_tls: bool = False
    resend_api_key: str = ""

    # ----------------------------------------------------------- analytics
    analytics_enabled: bool = True
    analytics_providers: CsvList = ["internal"]
    posthog_api_key: str = ""
    posthog_host: str = "https://eu.posthog.com"
    ga4_measurement_id: str = ""
    ga4_api_secret: str = ""
    yandex_metrica_counter_id: str = ""

    # ------------------------------------------------------- observability
    sentry_dsn: str = ""
    sentry_traces_sample_rate: float = 0.1
    otel_enabled: bool = False
    otel_exporter_otlp_endpoint: str = "http://localhost:4317"
    otel_service_name: str = "picglot-api"
    metrics_enabled: bool = True

    # ------------------------------------------------------- feature flags
    feature_batch: bool = True
    feature_public_api: bool = True
    feature_webhooks: bool = True
    feature_sharing: bool = True
    feature_teams: bool = True
    feature_pwa: bool = True
    feature_handwriting: bool = True
    feature_tables: bool = True
    feature_receipts: bool = True
    enabled_tools: CsvList = [
        "image-translator",
        "translate-photo",
        "screenshot-translator",
        "image-to-text",
        "jpg-to-word",
        "image-to-excel",
        "handwriting-to-text",
        "pdf-translator",
        "pdf-ocr",
        "document-scanner",
        "receipt-scanner",
        "invoice-ocr",
        "batch",
    ]

    # -------------------------------------------------------------- quotas
    guest_free_pages: int = 3
    free_monthly_credits: int = 10
    pro_monthly_credits: int = 1000
    business_monthly_credits: int = 5000

    # ---------------------------------------------------------------- seed
    # Development-only credentials. The passwords deliberately do not contain
    # the email local part — the strength check rejects that, correctly.
    seed_admin_email: str = "admin@picglot.example"
    seed_admin_password: str = "Sup3r!Seed-2026"
    seed_demo_email: str = "demo@picglot.example"
    seed_demo_password: str = "Tr1al!Seed-2026"
    seed_enabled: bool = True

    # ------------------------------------------------------------ helpers
    @field_validator(
        "secret_key_previous",
        "enabled_locales",
        "ocr_provider_priority",
        "ocr_handwriting_provider_priority",
        "ocr_table_provider_priority",
        "translation_provider_priority",
        "analytics_providers",
        "enabled_tools",
        mode="before",
    )
    @classmethod
    def _split_csv(cls, value: Any) -> list[str]:
        return _csv(value)

    @field_validator("log_level")
    @classmethod
    def _upper_log_level(cls, value: str) -> str:
        level = value.upper()
        if level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError(f"unsupported LOG_LEVEL {value!r}")
        return level

    @field_validator("public_web_url", "public_api_url")
    @classmethod
    def _strip_trailing_slash(cls, value: str) -> str:
        return value.rstrip("/")

    @model_validator(mode="after")
    def _validate_consistency(self) -> Settings:
        problems: list[str] = []

        if self.default_locale not in self.enabled_locales:
            problems.append(
                f"DEFAULT_LOCALE={self.default_locale!r} is not present in ENABLED_LOCALES="
                f"{','.join(self.enabled_locales)}"
            )
        if not self.ocr_provider_priority:
            problems.append("OCR_PROVIDER_PRIORITY must list at least one provider")
        if not self.translation_provider_priority:
            problems.append("TRANSLATION_PROVIDER_PRIORITY must list at least one provider")
        if self.job_soft_time_limit_seconds >= self.job_hard_time_limit_seconds:
            problems.append(
                "JOB_SOFT_TIME_LIMIT_SECONDS must be smaller than JOB_HARD_TIME_LIMIT_SECONDS"
            )
        if self.local_only_processing:
            external_ocr = set(self.ocr_provider_priority) - LOCAL_OCR_PROVIDERS
            if external_ocr and not (set(self.ocr_provider_priority) & LOCAL_OCR_PROVIDERS):
                problems.append(
                    "LOCAL_ONLY_PROCESSING=true but OCR_PROVIDER_PRIORITY contains no local "
                    "provider (rapidocr, tesseract)"
                )

        if self.environment == "production":
            problems.extend(self._production_problems())

        if problems:
            raise ValueError("Invalid configuration:\n  - " + "\n  - ".join(problems))
        return self

    def _production_problems(self) -> list[str]:
        problems: list[str] = []
        if self.secret_key == DEV_SECRET_KEY or len(self.secret_key) < 32:
            problems.append(
                "SECRET_KEY must be a unique value of at least 32 characters in production "
                '(generate with: python -c "import secrets;print(secrets.token_urlsafe(64))")'
            )
        if self.debug:
            problems.append("DEBUG must be false in production")
        if not self.session_cookie_secure:
            problems.append("SESSION_COOKIE_SECURE must be true in production")
        if self.log_format != "json":
            problems.append("LOG_FORMAT should be 'json' in production for structured logging")
        if self.public_web_url.startswith("http://"):
            problems.append("PUBLIC_WEB_URL must use https in production")
        if self.storage_backend == "local":
            problems.append(
                "STORAGE_BACKEND=local is not supported in production; use an S3 compatible store"
            )
        if self.s3_secret_access_key in {"picglot-dev-secret", ""}:
            problems.append("S3_SECRET_ACCESS_KEY still holds the development default")
        if self.billing_enabled and self.billing_provider == "stripe":
            if not self.stripe_secret_key:
                problems.append("STRIPE_SECRET_KEY is required when BILLING_PROVIDER=stripe")
            if not self.stripe_webhook_secret:
                problems.append(
                    "STRIPE_WEBHOOK_SECRET is required — unsigned webhooks are rejected"
                )
        if (
            self.billing_enabled
            and self.billing_provider == "yookassa"
            and not (self.yookassa_shop_id and self.yookassa_secret_key)
        ):
            problems.append(
                "YOOKASSA_SHOP_ID and YOOKASSA_SECRET_KEY are required when "
                "BILLING_PROVIDER=yookassa"
            )
        if self.email_provider == "console":
            problems.append("EMAIL_PROVIDER=console cannot deliver mail in production")
        if self.seed_enabled:
            problems.append("SEED_ENABLED must be false in production")
        return problems

    # --------------------------------------------------------- derivations
    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_test(self) -> bool:
        return self.environment == "test"

    @property
    def font_directory(self) -> Path:
        path = Path(self.font_dir)
        if not path.is_absolute():
            for base in (Path.cwd(), API_ROOT, REPO_ROOT):
                candidate = (base / path).resolve()
                if candidate.exists():
                    return candidate
        return path.resolve()

    @property
    def storage_root(self) -> Path:
        path = Path(self.local_storage_path)
        return path if path.is_absolute() else (REPO_ROOT / path).resolve()

    @property
    def s3_browser_endpoint(self) -> str:
        """Endpoint handed to browsers — differs from the in-cluster endpoint."""
        return (self.s3_public_endpoint_url or self.s3_endpoint_url).rstrip("/")

    @property
    def storage_endpoint_reachable_by_browser(self) -> bool:
        """Whether a presigned URL can actually be opened by a visitor.

        `S3_PUBLIC_ENDPOINT_URL` is empty by default, so the endpoint falls back
        to the in-cluster one — `http://minio:9000` under Compose. A URL signed
        against that host resolves for the API container and for nothing else:
        a phone cannot resolve `minio`, and a plain-http URL is blocked outright
        on an https page. Both cases look identical to the user (the picture
        never appears), so they are detected here and served through the API
        instead.
        """
        from urllib.parse import urlparse

        endpoint = urlparse(self.s3_browser_endpoint)
        host = (endpoint.hostname or "").lower()
        if not host or host in _UNROUTABLE_HOSTS or "." not in host:
            return False
        # Mixed content: an https page may not load an http subresource.
        return not (endpoint.scheme == "http" and self.public_web_url.startswith("https://"))

    @property
    def serve_files_through_api(self) -> bool:
        """Stream objects from the API rather than linking straight to storage."""
        return self.storage_backend == "local" or not self.storage_endpoint_reachable_by_browser

    def upload_limit_bytes(self, plan_code: str | None) -> int:
        return {
            None: self.max_upload_bytes_guest,
            "guest": self.max_upload_bytes_guest,
            "free": self.max_upload_bytes_free,
            "pro": self.max_upload_bytes_pro,
            "business": self.max_upload_bytes_business,
        }.get(plan_code, self.max_upload_bytes_free)

    def page_limit(self, plan_code: str | None) -> int:
        return {
            None: self.max_pdf_pages_guest,
            "guest": self.max_pdf_pages_guest,
            "free": self.max_pdf_pages_free,
            "pro": self.max_pdf_pages_pro,
            "business": self.max_pdf_pages_business,
        }.get(plan_code, self.max_pdf_pages_free)

    def batch_file_limit(self, plan_code: str | None) -> int:
        return {
            None: 1,
            "guest": 1,
            "free": self.max_batch_files_free,
            "pro": self.max_batch_files_pro,
            "business": self.max_batch_files_business,
        }.get(plan_code, self.max_batch_files_free)

    def retention_hours(self, plan_code: str | None) -> int:
        return {
            None: self.retention_guest_hours,
            "guest": self.retention_guest_hours,
            "free": self.retention_free_hours,
            "pro": self.retention_pro_hours,
            "business": self.retention_business_hours,
        }.get(plan_code, self.retention_free_hours)

    def signing_keys(self) -> list[str]:
        """Active key first, then any retired keys still accepted for verification."""
        return [self.secret_key, *self.secret_key_previous]


LOCAL_OCR_PROVIDERS = {"rapidocr", "tesseract", "builtin"}

#: Hosts that resolve inside the deployment and nowhere else. A URL signed
#: against one of these is useless to a browser.
_UNROUTABLE_HOSTS = {"minio", "localhost", "127.0.0.1", "0.0.0.0", "::1", "s3", "storage"}
LOCAL_TRANSLATION_PROVIDERS = {"argos", "echo"}


@functools.lru_cache(maxsize=1)
def get_settings() -> Settings:
    try:
        return Settings()
    except ValidationError as exc:  # pragma: no cover - startup path
        raise ConfigurationError(_format_validation_error(exc)) from exc


def _format_validation_error(exc: ValidationError) -> str:
    lines = ["PicGlot cannot start: the configuration is invalid.", ""]
    for error in exc.errors():
        location = ".".join(str(part) for part in error["loc"]) or "settings"
        lines.append(f"  [{location.upper()}] {error['msg']}")
    lines.append("")
    lines.append("Check your .env against .env.example, or see docs/operations/configuration.md")
    return "\n".join(lines)


def generate_secret_key() -> str:
    return secrets.token_urlsafe(64)


settings = get_settings()

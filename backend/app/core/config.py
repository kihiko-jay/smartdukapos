from pydantic_settings import BaseSettings
from typing import List


class Settings(BaseSettings):
    # App
    APP_NAME: str = "DukaPOS"
    APP_VERSION: str = "4.0.0"
    DEBUG: bool = False
    ALLOWED_ORIGINS: str = "http://localhost:3000,http://localhost:5173"

    # Database
    DATABASE_URL: str

    # Security
    SECRET_KEY: str
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30        # Short-lived access token (was 480 — a security hole)
    REFRESH_TOKEN_EXPIRE_HOURS: int = 9          # Refresh token covers a full 8-hour shift + buffer

    # Internal API key (protects /health/deep and /metrics in production)
    INTERNAL_API_KEY: str = ""   # Set to: openssl rand -hex 32

    # Redis (optional — enables multi-worker rate limiting, product cache, WS pub/sub)
    REDIS_URL: str = ""  # e.g. redis://:password@redis:6379/0

    # Rate limiting (requests per window)
    RATE_LIMIT_LOGIN_PER_MINUTE: int = 10        # Max login attempts per IP per minute
    RATE_LIMIT_API_PER_MINUTE: int = 300         # General API rate limit per token

    # Error tracking (optional — Sentry DSN for exception reporting)
    SENTRY_DSN: str = ""  # e.g. https://<key>@<org>.ingest.sentry.io/<project>

    # M-PESA
    MPESA_CONSUMER_KEY: str = ""
    MPESA_CONSUMER_SECRET: str = ""
    MPESA_SHORTCODE: str = "174379"
    MPESA_PASSKEY: str = ""
    MPESA_CALLBACK_URL: str = ""
    MPESA_ENV: str = "sandbox"

    # KRA eTIMS
    ETIMS_URL: str = "https://etims-api.kra.go.ke/etims-api"
    ETIMS_PIN: str = ""
    ETIMS_BRANCH_ID: str = "00"
    ETIMS_DEVICE_SERIAL: str = ""

    # Store
    STORE_NAME: str = "My Duka Store"
    STORE_LOCATION: str = "Nairobi, Kenya"
    STORE_TIMEZONE: str = "Africa/Nairobi"   # IANA tz — used for business-day boundaries
    VAT_RATE: float = 0.16
    CURRENCY: str = "KES"

    @property
    def origins(self) -> List[str]:
        return [o.strip() for o in self.ALLOWED_ORIGINS.split(",")]

    class Config:
        env_file = ".env"
        case_sensitive = True


settings = Settings()

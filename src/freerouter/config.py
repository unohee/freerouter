"""freerouter 설정 — 환경변수(.env)에서 로드."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """프록시 동작을 제어하는 설정값.

    `.env` 또는 프로세스 환경변수에서 읽는다. 키 이름은 그대로(대소문자 무시).
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # OpenRouter 인증/엔드포인트
    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"

    # 라우팅 동작
    # 무료 모델 목록을 다시 가져오기까지의 TTL(초)
    model_refresh_ttl: int = 600
    # 한 요청에서 폴백으로 시도할 최대 무료 모델 수.
    # 무료 모델은 수시로 rate-limit/크레딧 요구(402) 상태라 콜드 스타트에서
    # 상위 몇 개가 연속 실패할 수 있다 → 넉넉히 둔다.
    max_attempts: int = 8
    # 429(rate limit)를 맞은 모델을 건너뛰는 cooldown(초)
    cooldown_seconds: int = 60
    # 업스트림 요청 타임아웃(초)
    request_timeout: float = 120.0

    # OpenRouter 권장 식별 헤더(선택) — 사용량 대시보드/랭킹에 표기됨
    http_referer: str = "https://github.com/unohee/freerouter"
    x_title: str = "freerouter"

    # 서버 바인딩
    host: str = "127.0.0.1"
    port: int = 8000


settings = Settings()

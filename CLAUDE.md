# freerouter — 프로젝트 컨텍스트

## 목적

OpenRouter의 **무료 모델만** 라우팅하는 OpenAI 호환 프록시. 무료 티어의 핵심 고통(rate limit)을
여러 무료 모델 간 자동 폴백으로 완화하는 것이 존재 이유다.

## 스택

- Python ≥3.10, FastAPI + uvicorn(서버), httpx(async 업스트림), pydantic-settings(설정)
- src-layout: 패키지는 `src/freerouter/`

## 구조

| 파일 | 역할 |
|------|------|
| `config.py` | `.env`/환경변수 → `Settings`. 전역 `settings`. |
| `models.py` | `is_free()`/`is_chat_capable()` 판정 + `ModelRegistry`(무료 목록 TTL 캐시). |
| `router.py` | `FreeRouter` — 후보 순서 결정 + 429 cooldown 추적(순수, 상태=cooldown map). |
| `client.py` | `FreeRouterClient` — 폴백 라우팅 코어(async `chat`/`complete`/`stream_raw` + sync 래퍼). `RETRYABLE_STATUS` 정의. |
| `proxy.py` | FastAPI 앱: `/v1/chat/completions`(스트림 포함), `/v1/models`, `/health`. **client.py를 재사용**. |
| `__main__.py` | uvicorn 진입점(`freerouter` 콘솔 스크립트). |

**두 가지 사용 형태가 같은 코어(`client.py`)를 공유**: HTTP 프록시 서버(`proxy.py`)와
인프로세스 라이브러리(`from freerouter import FreeRouterClient`). 폴백 로직은 `client.py`에만 있다.
`models.py`/`router.py`에 전역 싱글톤 없음 — 인스턴스별 상태(라이브러리로 여러 클라이언트 독립 사용).

## 핵심 규칙 / 함정

- **무료 판정은 가격 기준**(`prompt==0 && completion==0`), `:free` 접미사가 아니다 —
  프로모 모델(예: `openrouter/owl-alpha`)은 접미사 없이 무료다.
- 폴백 트리거 상태코드: `proxy.RETRYABLE_STATUS`(408/409/429/5xx).
- 실제 사용 모델은 응답 헤더 `X-freerouter-Model`로 확인.
- OpenRouter 권장 식별 헤더(`HTTP-Referer`, `X-Title`)를 업스트림에 붙인다.
- `.env`는 절대 커밋하지 않는다(.gitignore 등록됨). 키는 `.env.example` 참고.

## 검증

- 단위테스트: `pytest` (무료 필터·라우팅 순서·cooldown).
- 라이브 확인: 서버 기동 후 `curl /v1/models`로 무료 목록, `/v1/chat/completions`로 라운드트립.

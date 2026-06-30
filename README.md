# freerouter

OpenRouter의 **무료 모델 엔드포인트만** 모아 자동 라우팅/폴백하는 OpenAI 호환 프록시.

기존 OpenAI SDK/클라이언트를 그대로 붙이면, freerouter가 OpenRouter의 무료 모델 풀
(`pricing.prompt == 0 && pricing.completion == 0`) 중에서 모델을 골라 호출하고,
rate limit(429)이나 일시적 오류가 나면 **다음 무료 모델로 자동 폴백**한다.

## 동작 방식

1. 시작 시 OpenRouter `/api/v1/models`를 가져와 **무료 모델만 필터링**해 캐시(TTL 기본 600초).
2. `POST /v1/chat/completions` 요청이 오면 라우터가 후보 순서를 만든다.
   - `model`이 특정 무료 모델이면 그것을 맨 앞에, 그 외(`auto`/빈 값/유료/미보유)는 무료 풀 우선순위(컨텍스트 큰 순).
   - 직전에 429를 맞은 모델은 cooldown(기본 60초) 동안 뒤로 밀림.
3. 후보를 순서대로 시도하다 429/5xx면 다음 모델로 폴백. 모두 실패하면 502.
4. 응답 헤더 `X-freerouter-Model`에 실제로 사용된 모델 id를 담아 돌려준다.

## 설치

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env   # OPENROUTER_API_KEY 채우기
```

## 실행

```bash
freerouter            # 또는: python -m freerouter
# 기본 http://127.0.0.1:8000
```

## 사용 (OpenAI SDK 그대로)

```python
from openai import OpenAI

client = OpenAI(base_url="http://127.0.0.1:8000/v1", api_key="unused")

resp = client.chat.completions.create(
    model="auto",  # freerouter가 무료 풀에서 자동 선택
    messages=[{"role": "user", "content": "안녕"}],
)
print(resp.choices[0].message.content)
```

curl 예시:

```bash
curl http://127.0.0.1:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"auto","messages":[{"role":"user","content":"hi"}]}'
```

## 엔드포인트

| 메서드 | 경로 | 설명 |
|--------|------|------|
| POST | `/v1/chat/completions` | OpenAI 호환. 스트리밍(`"stream": true`) 지원. |
| GET | `/v1/models` | 라우팅 가능한 무료 모델만 노출. |
| GET | `/health` | 헬스체크. |

## 설정

`.env` 또는 환경변수로 제어한다. 전체 목록은 `src/freerouter/config.py` 참조.

| 키 | 기본값 | 설명 |
|----|--------|------|
| `OPENROUTER_API_KEY` | (필수) | OpenRouter 키 |
| `MODEL_REFRESH_TTL` | 600 | 무료 목록 캐시 TTL(초) |
| `MAX_ATTEMPTS` | 8 | 폴백 시도 최대 모델 수 |
| `COOLDOWN_SECONDS` | 60 | 429 맞은 모델 제외 시간(초) |

## 테스트

```bash
pytest
```

## 라이선스

MIT — [LICENSE](LICENSE) 참조.

"""LLM 게이트웨이 실패를 사람이 읽을 수 있는 문장으로 바꾼다.

한도 초과·인증 거부·연결 실패는 사용자가 할 수 있는 조치가 서로 다르다. 원문(코드·본문)은
디버깅에 필요하므로 괄호 안에 남기고, 앞에 무엇을 해야 하는지 한 줄을 붙인다.
CLI와 웹이 같은 문장을 쓰도록 여기 한 곳에 둔다.
"""

from __future__ import annotations

LIMIT_MARKERS = ("spending limit", "rate limit", "quota", "429", "too many requests")
AUTH_MARKERS = ("401", "unauthorized", "invalid api key", "invalid_api_key", "authentication")
NETWORK_MARKERS = ("connection", "timeout", "timed out", "getaddrinfo", "name or service not known")


def gateway_hint(text: str) -> str:
    lowered = text.lower()
    if any(marker in lowered for marker in LIMIT_MARKERS):
        return f"LLM 게이트웨이 사용량 한도에 걸렸습니다. 한도가 초기화된 뒤 다시 시도해 주세요. ({text})"
    if any(marker in lowered for marker in AUTH_MARKERS):
        return f"LLM 게이트웨이가 인증을 거부했습니다. PAT이 만료되지 않았는지 확인해 주세요. ({text})"
    if any(marker in lowered for marker in NETWORK_MARKERS):
        return f"LLM 게이트웨이에 연결하지 못했습니다. CLUEFIN_LLM_BASE_URL과 네트워크를 확인해 주세요. ({text})"
    return text

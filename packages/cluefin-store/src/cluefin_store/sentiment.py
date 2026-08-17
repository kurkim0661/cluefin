from __future__ import annotations

import html
import json
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime
from email.utils import parsedate_to_datetime
from uuid import UUID

from cluefin_store.models import SymbolSentimentItem

BULLISH_TERMS = (
    "호실적",
    "수주",
    "계약",
    "증설",
    "상승",
    "급등",
    "목표가 상향",
    "매수",
    "흑자전환",
    "실적 개선",
    "신고가",
    "배당",
    "자사주",
    "투자 확대",
)
BEARISH_TERMS = (
    "하락",
    "급락",
    "부진",
    "적자",
    "리콜",
    "소송",
    "조사",
    "과징금",
    "목표가 하향",
    "매도",
    "감산",
    "파업",
    "실적 악화",
)


@dataclass(frozen=True, slots=True)
class SearchResult:
    title: str
    url: str
    source: str | None = None
    published_at: datetime | None = None
    summary: str = ""
    raw: dict | None = None


@dataclass(frozen=True, slots=True)
class SentimentResult:
    label: str
    score: float
    reason: str


class GoogleNewsRssSearchProvider:
    provider_name = "google_news_rss"

    def __init__(self, timeout_seconds: float = 10.0) -> None:
        self.timeout_seconds = timeout_seconds

    def search(self, query: str, *, limit: int = 10) -> list[SearchResult]:
        params = urllib.parse.urlencode({"q": query, "hl": "ko", "gl": "KR", "ceid": "KR:ko"})
        url = f"https://news.google.com/rss/search?{params}"
        request = urllib.request.Request(url, headers={"User-Agent": "cluefin-store/0.1"})
        with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
            payload = response.read()
        root = ET.fromstring(payload)
        results: list[SearchResult] = []
        for item in root.findall("./channel/item")[:limit]:
            title = _text(item, "title")
            link = _text(item, "link")
            summary = html.unescape(_text(item, "description"))
            source_node = item.find("source")
            published_at = _parse_rss_datetime(_text(item, "pubDate"))
            results.append(
                SearchResult(
                    title=html.unescape(title),
                    url=link,
                    source=html.unescape(source_node.text) if source_node is not None and source_node.text else None,
                    published_at=published_at,
                    summary=summary,
                    raw={"title": title, "link": link, "description": summary},
                )
            )
        return results


def classify_sentiment(title: str, summary: str = "") -> SentimentResult:
    text = f"{title} {summary}".lower()
    bullish = [term for term in BULLISH_TERMS if term.lower() in text]
    bearish = [term for term in BEARISH_TERMS if term.lower() in text]
    score = min(1.0, len(bullish) * 0.35) - min(1.0, len(bearish) * 0.35)
    if score > 0.15:
        label = "bullish"
        reason = f"긍정 키워드 감지: {', '.join(bullish)}"
    elif score < -0.15:
        label = "bearish"
        reason = f"부정 키워드 감지: {', '.join(bearish)}"
    else:
        label = "neutral"
        reason = "명확한 긍정/부정 키워드가 부족함"
    return SentimentResult(label=label, score=score, reason=reason)


def build_sentiment_items(
    *,
    symbol: str,
    name: str,
    provider_name: str,
    results: list[SearchResult],
    run_id: UUID,
    collected_at: datetime,
) -> list[SymbolSentimentItem]:
    query = sentiment_query(symbol, name)
    items: list[SymbolSentimentItem] = []
    for result in results:
        sentiment = classify_sentiment(result.title, result.summary)
        items.append(
            SymbolSentimentItem(
                symbol=symbol,
                provider=provider_name,
                query=query,
                title=result.title,
                source=result.source,
                url=result.url,
                published_at=result.published_at,
                summary=result.summary,
                sentiment_label=sentiment.label,
                sentiment_score=sentiment.score,
                sentiment_reason=sentiment.reason,
                raw_json=json.dumps(result.raw or {}, ensure_ascii=False, sort_keys=True),
                run_id=run_id,
                collected_at=collected_at,
            )
        )
    return items


def sentiment_query(symbol: str, name: str) -> str:
    query_name = name.strip() or symbol
    return f"{query_name} {symbol} 주식 실적 수주 목표가"


def _text(node: ET.Element, child: str) -> str:
    found = node.find(child)
    return found.text or "" if found is not None else ""


def _parse_rss_datetime(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    return parsed.replace(tzinfo=None)

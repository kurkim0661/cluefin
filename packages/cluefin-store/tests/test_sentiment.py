from datetime import datetime
from uuid import UUID

from cluefin_store.sentiment import SearchResult, build_sentiment_items, classify_sentiment

RUN_ID = UUID("00000000-0000-0000-0000-000000000001")
COLLECTED_AT = datetime(2026, 8, 17, 9, 0, 0)


def test_classify_sentiment_tags_bullish_with_reason() -> None:
    result = classify_sentiment("삼성전자 호실적에 목표가 상향", "AI 수요로 실적 개선")

    assert result.label == "bullish"
    assert result.score > 0
    assert "호실적" in result.reason


def test_classify_sentiment_tags_bearish_with_reason() -> None:
    result = classify_sentiment("SK하이닉스 급락", "실적 악화와 목표가 하향")

    assert result.label == "bearish"
    assert result.score < 0
    assert "급락" in result.reason


def test_build_sentiment_items_records_query_source_and_reason() -> None:
    items = build_sentiment_items(
        symbol="005930",
        name="삼성전자",
        provider_name="google_news_rss",
        results=[
            SearchResult(
                title="삼성전자 수주 확대",
                url="https://example.com/news",
                source="Example",
                published_at=COLLECTED_AT,
                summary="계약 소식",
                raw={"id": "1"},
            )
        ],
        run_id=RUN_ID,
        collected_at=COLLECTED_AT,
    )

    assert items[0].symbol == "005930"
    assert items[0].query == "삼성전자 005930 주식 실적 수주 목표가"
    assert items[0].sentiment_label == "bullish"
    assert "수주" in items[0].sentiment_reason

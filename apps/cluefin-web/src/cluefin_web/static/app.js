const state = {
  dashboard: null,
  pulse: null,
  researchReport: null,
  paper: null,
  activeTab: "pulse",
  pulseFilter: "all",
  selectedIndicatorId: null,
  indicatorRange: 90,
  indicatorOffset: 0,
  indicatorHoverIndex: null,
  indicatorDrag: null,
  selectedSymbol: null,
  chart: null,
  chartWindow: 120,
  chartOffset: 0,
  chartYScale: 1,
  chartDrag: null,
  chartYDrag: null,
  enabledPatternTypes: new Set(),
  selectedPatternEventKey: "",
  indicators: {
    vwap: false,
    ema_20: false,
    ema_50: false,
    ema_200: false,
    volume: false,
    rsi: false,
    profile: false,
    patterns: false,
  },
  sort: {},
};

const CHART_Y_SCALE_MIN = 0.35;
const CHART_Y_SCALE_MAX = 2.5;

const formatPercent = value => {
  if (value === null || value === undefined || Number.isNaN(Number(value))) {
    return "-";
  }
  return `${(Number(value) * 100).toFixed(1)}%`;
};

const formatNumber = value => new Intl.NumberFormat("en-US").format(Number(value || 0));

const tag = value => `<span class="tag ${value ? "" : "no"}">${value ? "Yes" : "No"}</span>`;

async function loadDashboard() {
  const [dashboardResponse, pulseResponse, reportResponse] = await Promise.all([
    fetch("/api/dashboard"),
    fetch("/api/market-pulse"),
    fetch("/api/research-report"),
  ]);
  state.dashboard = await dashboardResponse.json();
  state.pulse = pulseResponse.ok ? await pulseResponse.json() : null;
  state.researchReport = reportResponse.ok ? await reportResponse.json() : null;
  render();
}

function render() {
  const data = state.dashboard || {
    overview: {},
    performance: [],
    signals: [],
    universe: [],
    technicals: [],
    sentiment: [],
  };
  const overview = data.overview || {};
  document.querySelector("#metric-symbols").textContent = formatNumber(overview.symbols);
  document.querySelector("#metric-events").textContent = formatNumber(overview.events);
  document.querySelector("#metric-outcomes").textContent = formatNumber(overview.outcomes);
  document.querySelector("#metric-signals").textContent = formatNumber((data.signals || []).length);
  const contextAsOf = state.pulse?.as_of || overview.as_of;
  document.querySelector("#as-of").textContent = contextAsOf ? `시장 데이터 ${contextAsOf} 기준` : "수집된 시장 데이터가 없습니다";
  renderMarketPulse(state.pulse);
  renderResearchReport(state.researchReport);
  renderSnapshot(data);
  renderSignals(data.signals || []);
  renderPerformance(data.performance || []);
  renderUniverse(data.universe || [], data.technicals || [], data.sentiment || [], data.signals || []);
  updateChartSymbolOptions(data.universe || []);
  setActiveTab(state.activeTab);
}

function renderResearchReport(report) {
  const status = document.querySelector("#ai-report-status");
  const content = document.querySelector("#ai-report-content");
  if (!status || !content) return;
  if (!report) {
    status.className = "freshness missing";
    status.textContent = "리포트 대기";
    content.className = "ai-report-content empty";
    content.innerHTML = `<strong>아직 생성된 리포트가 없습니다</strong><p>PAT 환경을 연결한 뒤 <code>uv run cluefin-agent daily-report</code>를 실행하면 이곳에 최신 리포트가 표시됩니다.</p>`;
    return;
  }
  status.className = `freshness ${report.status === "validated" ? "fresh" : "aging"}`;
  status.textContent = `${report.report_date} · ${report.model}`;
  content.className = "ai-report-content";
  content.innerHTML = `<div class="ai-report-meta"><strong>${escapeHtml(report.title)}</strong><span>${escapeHtml(report.generated_at)} · ${escapeHtml(report.status)}</span></div><div class="ai-report-markdown">${safeMarkdown(report.markdown)}</div>`;
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>'"]/g, character => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" })[character]);
}

function safeMarkdown(markdown) {
  const lines = String(markdown || "").split(/\r?\n/);
  let listOpen = false;
  const html = [];
  lines.forEach(line => {
    const value = escapeHtml(line.trim());
    if (value.startsWith("## ")) {
      if (listOpen) { html.push("</ul>"); listOpen = false; }
      html.push(`<h3>${value.slice(3)}</h3>`);
    } else if (value.startsWith("# ")) {
      if (listOpen) { html.push("</ul>"); listOpen = false; }
      html.push(`<h2>${value.slice(2)}</h2>`);
    } else if (value.startsWith("- ")) {
      if (!listOpen) { html.push("<ul>"); listOpen = true; }
      html.push(`<li>${value.slice(2)}</li>`);
    } else if (value) {
      if (listOpen) { html.push("</ul>"); listOpen = false; }
      html.push(`<p>${value}</p>`);
    }
  });
  if (listOpen) html.push("</ul>");
  return html.join("");
}

function renderMarketPulse(pulse) {
  const empty = {
    regime: { key: "mixed", label: "데이터 대기", headline: "시장 지표를 아직 수집하지 않았습니다", summary: "cluefin-store update-indicators를 실행하면 이 화면이 채워집니다.", score: 0 },
    coverage: { observed: 0, total: 0, fresh: 0, public_missing: 0, connection_required: 0 },
    sections: {},
    drivers: [],
    indicators: [],
    unavailable: [],
  };
  const data = pulse || empty;
  const regime = data.regime || empty.regime;
  const regimeNode = document.querySelector("#pulse-regime");
  regimeNode.textContent = regime.label || "혼조";
  regimeNode.className = `regime-badge ${regime.key || "mixed"}`;
  document.querySelector("#pulse-headline").textContent = regime.headline || empty.regime.headline;
  document.querySelector("#pulse-summary").textContent = regime.summary || empty.regime.summary;
  document.querySelector("#pulse-score").textContent = String(Math.round(Number(regime.score || 0) * 100));

  const sectionLabels = { rates_liquidity: "금리와 유동성", growth_prices: "경기와 물가", korea: "한국 시장", equity: "기업 실적", crypto: "디지털 자산" };
  Object.entries(sectionLabels).forEach(([key]) => {
    const card = document.querySelector(`[data-context="${key}"]`);
    const section = (data.sections || {})[key] || { label: "대기", tone: "neutral", observed: 0, total: 0 };
    card.classList.remove("positive", "negative", "neutral");
    card.classList.add(section.tone || "neutral");
    card.querySelector("strong").textContent = section.label || "대기";
    card.querySelector("small").textContent = `${section.observed || 0}/${section.total || 0}개 지표 연결`;
  });

  const coverage = data.coverage || empty.coverage;
  const ratio = coverage.total ? (coverage.observed / coverage.total) * 100 : 0;
  document.querySelector("#coverage-ratio").textContent = `${coverage.observed || 0} / ${coverage.total || 0}`;
  document.querySelector("#coverage-bar").style.width = `${Math.min(100, Math.max(0, ratio))}%`;
  document.querySelector("#coverage-copy").textContent = coverage.total
    ? `최신 ${coverage.fresh || 0}개 · 공개 데이터 대기 ${coverage.public_missing || 0}개 · 별도 연결 ${coverage.connection_required || 0}개`
    : "먼저 지표 카탈로그를 ClickHouse에 초기화해 주세요.";
  renderConnectionList(data.unavailable || []);
  renderDriverGrid(data);
}

function renderDriverGrid(pulse) {
  const allIndicators = pulse.indicators || [];
  const rows = state.pulseFilter === "all"
    ? (pulse.drivers || [])
    : allIndicators
        .filter(item => item.domain === state.pulseFilter)
        .sort((a, b) => Number(b.importance || 0) - Number(a.importance || 0));
  const grid = document.querySelector("#driver-grid");
  grid.innerHTML = rows.length
    ? rows.map(renderDriverCard).join("")
    : `<article class="driver-empty"><strong>표시할 지표가 없습니다</strong><p>수집 파이프라인을 실행하거나 다른 영역을 선택해 주세요.</p></article>`;
  initializeIndicatorCards();
}

function renderDriverCard(item) {
  const hasValue = item.value !== null && item.value !== undefined;
  const changeClass = item.tone || "neutral";
  const change = item.change_pct === null || item.change_pct === undefined
    ? "비교값 없음"
    : `${item.change_pct >= 0 ? "+" : ""}${(Number(item.change_pct) * 100).toFixed(2)}%`;
  const source = item.source_url
    ? `<a href="${item.source_url}" target="_blank" rel="noreferrer">${item.provider}</a>`
    : `<span>${item.provider || "source"}</span>`;
  const impactScore = item.impact_score === null || item.impact_score === undefined ? null : Number(item.impact_score);
  const marker = impactScore === null ? 50 : Math.max(3, Math.min(97, (impactScore + 100) / 2));
  const impactLabel = item.impact_label || (hasValue ? "판단 보조" : "데이터 없음");
  const percentile = item.percentile === null || item.percentile === undefined
    ? "위치 계산 전"
    : `최근 범위 ${Number(item.percentile)}백분위`;
  const learning = indicatorLearningGuide(item);
  return `<article class="driver-card ${hasValue ? "" : "missing"} impact-${changeClass}" data-indicator-id="${item.indicator_id}" tabindex="0" aria-label="${item.name_ko} 차트 열기">
    <div class="driver-card-head">
      <div><span>${domainLabel(item.domain)} · ${categoryLabel(item.category)}</span><h3>${item.name_ko}</h3></div>
      <span class="freshness ${item.freshness || "missing"}">${freshnessLabel(item.freshness, item.availability)}</span>
    </div>
    <div class="impact-verdict ${changeClass}">
      <span>최근 영향</span>
      <strong>${impactLabel}</strong>
      <em>${impactScore === null ? "맥락" : `${impactScore > 0 ? "+" : ""}${impactScore}`}</em>
    </div>
    <div class="impact-meter ${changeClass}" aria-label="시장 영향도 ${impactLabel}">
      <span class="impact-meter-negative">부담</span>
      <span class="impact-meter-neutral">중립</span>
      <span class="impact-meter-positive">우호</span>
      <i style="left:${marker}%"></i>
    </div>
    <p class="impact-explanation">${item.impact_explanation || "충분한 비교 데이터가 필요합니다."}</p>
    <div class="driver-value-row">
      <strong>${hasValue ? formatIndicatorValue(item.value, item.unit) : "연결 필요"}</strong>
      <span class="driver-change ${changeClass}">${hasValue ? change : availabilityLabel(item.availability)}</span>
    </div>
    ${sparkline(item.series || [], changeClass)}
    <div class="impact-context"><span>${item.comparison_label || "직전 관측"} 비교</span><span class="${item.level_tone || "neutral"}">${item.level_label || percentile}</span><span>신뢰도 ${confidenceLabel(item.impact_confidence)}</span></div>
    <details class="indicator-help"><summary>이 지표가 뭔가요? 왜 이렇게 해석하나요?</summary><div class="indicator-help-detail"><p><strong>무엇인가</strong>${learning.what}</p><p><strong>왜 중요한가</strong>${item.interpretation_ko || item.description_ko || ""}</p><p><strong>예를 들면</strong>${learning.example}</p><p><strong>주의할 점</strong>${learning.caveat}</p></div></details>
    <footer><span>${item.period || "관측값 없음"}</span><div>${source}<button type="button" class="indicator-chart-button">차트 보기</button></div></footer>
  </article>`;
}

function indicatorLearningGuide(item) {
  const specific = {
    usd_krw: { example: "원·달러가 1,450원에서 1,380원으로 내려오면 외국인 입장에서 환차손 우려가 줄고 한국 주식 수급에 우호적일 수 있습니다.", caveat: "수출기업은 원화 약세에서 이익을 볼 수 있어 업종별 영향은 반대일 수 있습니다." },
    broad_usd: { example: "광의 달러지수가 빠르게 하락하면 달러로 빌린 자금의 부담이 줄어 신흥국 주식과 코인으로 자금이 이동하기 쉬워집니다.", caveat: "달러 하락이 미국 경기침체 우려에서 나온 것이라면 위험자산에 반드시 호재는 아닙니다." },
    us_real_yield_10y: { example: "실질금리가 2.5%에서 2.0%로 낮아지면 미래 이익의 현재가치가 커져 성장주와 코인 밸류에이션 부담이 완화됩니다.", caveat: "최근 하락이 우호적이어도 절대 수준이 과거 상단이면 여전히 부담일 수 있어 현재 백분위를 같이 봐야 합니다." },
    us_initial_claims: { example: "신규 실업수당이 20만건에서 26만건으로 몇 주 연속 늘면 고용 둔화가 시작될 가능성이 커져 기업 실적 전망에 부담이 됩니다.", caveat: "한 주 급등은 휴일·파업·날씨 영향일 수 있으므로 4주 추세를 봅니다." },
    stablecoin_supply: { example: "스테이블코인 공급이 한 달간 증가하면 거래소 밖에서 코인을 살 수 있는 대기성 달러가 늘어난 것으로 해석할 수 있습니다.", caveat: "발행 증가가 실제 거래소 유입이나 매수로 이어지지 않을 수 있습니다." },
    btc_mvrv: { example: "MVRV가 1이면 시장가격이 전체 보유자의 추정 취득원가와 비슷하고, 크게 높아질수록 미실현 이익과 차익실현 압력이 커질 수 있습니다.", caveat: "정확한 고점 숫자는 사이클마다 달라 단독 매매 신호로 쓰면 안 됩니다." },
    btc_hashrate: { example: "해시레이트가 600 EH/s에서 900 EH/s로 늘면 더 많은 채굴 장비가 경쟁하고 있어 네트워크 공격 비용과 채굴 난이도가 높아지는 방향입니다.", caveat: "채굴 장비는 가격이 하락해도 바로 꺼지지 않아 해시레이트는 가격보다 늦게 움직입니다. 단기 가격 상승 신호가 아닙니다." },
    btc_realized_price: { example: "비트코인 가격이 8만달러이고 실현가격이 5만달러라면 시장 전체는 평균적으로 이익권입니다. 가격이 실현가격 아래로 내려가면 광범위한 손실 구간일 수 있습니다.", caveat: "거래소 내부 이동도 마지막 이동으로 계산될 수 있고 모든 투자자의 실제 매수가를 정확히 뜻하지는 않습니다." },
    btc_nupl: { example: "NUPL이 0.4라면 시가총액 대비 순미실현 이익이 약 40%인 상태로 해석합니다. 0 아래로 내려가면 시장 전체 손실이 우세한 구간입니다.", caveat: "높고 낮음의 극단 기준은 시장 사이클마다 달라 고정 숫자로 고점과 저점을 단정하면 안 됩니다." },
    crypto_funding_rate: { example: "펀딩비가 계속 큰 양수면 롱 포지션이 숏에게 비용을 내고 있다는 뜻이라 상승 베팅이 한쪽으로 몰렸고 급락 시 연쇄청산 위험이 커질 수 있습니다.", caveat: "양수 펀딩이 강한 수요를 뜻할 때도 있어 그 자체로 하락 신호는 아닙니다. 가격과 미결제약정을 함께 봅니다." },
    crypto_open_interest: { example: "비트코인 가격과 미결제약정이 동시에 급증하면 새 레버리지 포지션이 상승을 추격하는 상황일 수 있습니다. 가격은 오르는데 OI가 줄면 숏 청산 영향일 수 있습니다.", caveat: "Binance 한 거래소 데이터이므로 전체 시장을 완전히 대표하지 않으며 롱과 숏 방향도 OI만으로 알 수 없습니다." },
    fed_net_liquidity: { example: "연준 자산이 그대로여도 TGA와 역레포 잔액이 함께 줄면 비공식 순유동성 대용치는 늘어 위험자산에 쓸 수 있는 달러가 증가한 것으로 해석할 수 있습니다.", caveat: "공식 지표가 아니고 은행대출·해외 중앙은행·재정지출을 모두 담지 못하므로 가격과 일대일로 연결하면 안 됩니다." },
    us_treasury_tga: { example: "재무부가 대규모 국채를 발행해 TGA를 5천억달러에서 8천억달러로 채우면 투자자 현금이 정부 계좌로 이동해 단기 유동성을 흡수할 수 있습니다.", caveat: "이후 정부가 지출하면 다시 민간으로 풀리므로 잔액 방향과 재정 일정을 같이 봐야 합니다." },
    fed_overnight_rrp: { example: "역레포 잔액이 1조달러에서 빠르게 감소하면 머니마켓펀드 현금이 국채나 다른 시장으로 이동할 여지가 생긴 것으로 볼 수 있습니다.", caveat: "잔액 감소분이 반드시 주식이나 코인 매수로 들어가는 것은 아닙니다." },
    us_nfci: { example: "NFCI가 -0.5면 역사적 평균보다 금융여건이 느슨하고, +0.5면 금리·신용·은행 조건이 평균보다 긴축적이라는 뜻입니다.", caveat: "여러 시장가격을 포함하므로 주가 하락을 원인이라기보다 결과로 다시 반영하는 부분이 있습니다." },
    us_high_yield_oas: { example: "하이일드 OAS가 3%p에서 6%p로 뛰면 저신용 기업이 국채보다 훨씬 높은 이자를 내야 해 부도와 경기침체 우려가 커졌다는 뜻입니다.", caveat: "이미 공포가 극단인 시점에는 스프레드 축소가 주가보다 늦게 나타날 수 있습니다." },
  };
  if (specific[item.indicator_id]) return { what: indicatorWhat(item), ...specific[item.indicator_id] };
  const category = {
    rates: { example: "금리가 하락하면 같은 기업이익에도 적용되는 할인율이 낮아져 주식의 적정가치가 높아질 수 있습니다.", caveat: "경기침체 때문에 금리가 내려가는 경우에는 실적 악화가 금리 효과를 상쇄할 수 있습니다." },
    inflation: { example: "물가가 예상보다 높으면 중앙은행의 금리 인하가 늦어져 성장주와 고위험 자산이 압박받을 수 있습니다.", caveat: "발표값보다 시장 예상과의 차이와 최근 3개월 추세가 더 중요합니다." },
    growth: { example: "수출·생산·소비가 함께 개선되면 기업 매출과 이익 추정치가 올라갈 가능성이 커집니다.", caveat: "너무 강한 성장은 물가와 금리를 다시 끌어올릴 수 있습니다." },
    labor: { example: "고용이 완만하게 둔화하면 금리 부담은 줄고 소비는 유지되는 연착륙 환경이 될 수 있습니다.", caveat: "급격한 악화는 금리 호재보다 침체 충격이 더 큽니다." },
    liquidity: { example: "시장에 사용 가능한 달러 유동성이 늘면 주식·코인 같은 위험자산의 매수 여력이 커질 수 있습니다.", caveat: "유동성과 가격의 시차가 일정하지 않고 정책 기대가 먼저 반영될 수 있습니다." },
    credit: { example: "신용스프레드가 벌어지면 기업 조달비용과 부도 우려가 높아져 주식시장에 부담이 됩니다.", caveat: "스프레드는 시장가격 기반이라 악재를 이미 반영한 뒤 움직일 수 있습니다." },
    onchain: { example: "온체인 활동과 가치평가를 함께 보면 가격 상승이 실제 네트워크 사용을 동반하는지 구분할 수 있습니다.", caveat: "거래소 내부 이동·봇·주소 중복 때문에 주소 수만으로 사용자를 판단할 수 없습니다." },
    protocol: { example: "TVL뿐 아니라 수수료와 프로토콜 수익이 같이 늘면 실제 사용과 가치 포착이 동반된 성장일 가능성이 높습니다.", caveat: "토큰 가격 상승만으로 달러 환산 TVL이 증가할 수 있습니다." },
  }[item.category];
  const guide = category || { example: "최근 방향, 현재 수준, 다른 관련 지표가 같은 방향인지 함께 비교하면 해석 신뢰도가 높아집니다.", caveat: "한 지표만으로 매수·매도를 결정하지 말고 시장 예상과 데이터 수정 여부를 확인하세요." };
  return { what: indicatorWhat(item), ...guide };
}

function indicatorWhat(item) {
  const specific = {
    btc_hashrate: "전 세계 비트코인 채굴기가 초당 수행하는 계산량의 추정치입니다. TH/s가 높을수록 블록을 공격하거나 거래 기록을 뒤집는 데 더 많은 비용이 듭니다.",
    btc_mvrv: "비트코인의 현재 시가총액을 각 코인이 마지막으로 이동했을 때의 가격으로 계산한 실현 시가총액으로 나눈 값입니다. 시장 전체의 미실현 손익 배율에 가깝습니다.",
    btc_realized_price: "모든 비트코인의 추정 평균 취득원가입니다. 각 코인이 마지막으로 이동한 가격을 기준으로 계산해 단순 평균 매수가격과는 다릅니다.",
    btc_nupl: "비트코인 전체 공급이 가진 미실현 이익에서 미실현 손실을 뺀 뒤 시가총액으로 나눈 값입니다. 0 위면 시장 전체가 대체로 이익, 아래면 손실 상태입니다.",
    btc_active_addresses: "하루 동안 비트코인을 보내거나 받은 고유 주소 수입니다. 사람 수가 아니라 블록체인 주소 수입니다.",
    stablecoin_supply: "달러 등 법정화폐 가치에 연동된 스테이블코인이 시장에 얼마나 발행돼 있는지를 달러로 합산한 값입니다.",
    defi_tvl: "DeFi 스마트계약에 예치된 자산의 달러 환산 가치입니다. 예금 잔액과 비슷해 보이지만 같은 자산이 여러 프로토콜에서 중복 계산될 수 있습니다.",
    defi_fees: "사용자가 DeFi 거래·대출·스왑을 위해 실제로 지불한 하루 수수료의 합계입니다.",
    defi_revenue: "사용자 수수료 중 유동성 공급자나 검증자가 아니라 프로토콜 또는 토큰 보유자에게 귀속되는 몫입니다.",
    crypto_funding_rate: "무기한 선물 가격을 현물 가격 근처에 붙여두기 위해 롱과 숏 보유자가 서로 주고받는 정기 비용입니다. 양수면 보통 롱이 숏에게 지급합니다.",
    crypto_open_interest: "아직 청산되거나 만기되지 않은 선물 포지션의 총 달러 가치입니다. 시장에 쌓인 레버리지 규모를 뜻합니다.",
    fed_net_liquidity: "연준 총자산에서 미 재무부 계좌(TGA)와 연준 역레포 잔액을 뺀 비공식 유동성 대용치입니다. 공식 연준 지표는 아닙니다.",
    us_treasury_tga: "미국 재무부가 연준에 보유한 정부의 당좌계좌 잔액입니다. 정부가 세금·국채대금을 받아 잔액을 늘리면 민간 달러가 일시적으로 빠질 수 있습니다.",
    fed_overnight_rrp: "금융기관이 하루 동안 현금을 연준에 맡기고 국채를 담보로 받는 거래 잔액입니다. 잔액이 크면 현금이 연준 시설에 머물러 있다는 뜻입니다.",
    us_nfci: "시카고 연은이 금리·신용스프레드·주가·은행 지표 등 100개 이상을 합쳐 만든 금융여건지수입니다. 0보다 높으면 역사적 평균보다 긴축적입니다.",
    us_high_yield_oas: "신용등급이 낮은 미국 회사채 금리가 비슷한 만기의 국채보다 얼마나 더 높은지를 나타내는 가산금리입니다. 부도 위험에 대한 시장 보험료에 가깝습니다.",
    us_curve_10y_3m: "미국 10년 국채금리에서 3개월 금리를 뺀 값입니다. 장기 성장 기대와 단기 정책금리의 관계를 보여줍니다.",
    us_breakeven_10y: "명목 국채금리에서 물가연동국채 실질금리를 뺀 값으로, 채권시장이 반영한 향후 10년 평균 물가 기대의 대용치입니다.",
    us_real_yield_10y: "미국 10년 국채금리에서 시장의 기대인플레이션을 제거한 실질 수익률입니다. 물가를 제외하고도 얻을 수 있는 무위험 보상에 가깝습니다.",
  };
  if (specific[item.indicator_id]) return specific[item.indicator_id];
  const byCategory = {
    rates: "돈을 빌리거나 미래 현금흐름을 현재 가치로 바꿀 때 적용되는 금리 관련 지표입니다.",
    liquidity: "금융시스템 안에서 투자·대출·결제에 사용할 수 있는 현금과 준비금의 규모를 보여주는 지표입니다.",
    inflation: "상품과 서비스 가격이 얼마나 빠르게 변하는지 보여주는 물가 지표입니다.",
    growth: "생산·소비·수출·주문처럼 실물경제 활동이 늘거나 줄어드는 속도를 보여주는 지표입니다.",
    labor: "고용, 실업, 임금 등 가계 소득과 소비 여력을 보여주는 노동시장 지표입니다.",
    credit: "기업과 가계가 돈을 빌릴 수 있는 조건과 시장이 평가하는 부도 위험을 보여주는 지표입니다.",
    fx: "한 통화가 다른 통화와 교환되는 비율 또는 여러 통화에 대한 달러의 상대적 가치를 보여주는 환율 지표입니다.",
    commodities: "원유·금·구리처럼 생산과 물가, 안전자산 수요에 영향을 주는 원자재 가격 지표입니다.",
    earnings: "기업이 매출과 이익을 얼마나 만들고 있는지, 여러 기업 중 개선되는 기업 비율이 얼마나 되는지를 보여주는 실적 지표입니다.",
    valuation: "기업의 가격이 예상 이익이나 현금흐름에 비해 비싼지 싼지를 비교하는 가치평가 지표입니다.",
    market: "해당 자산시장의 전체 가격 수준, 시가총액 또는 거래 규모를 보여주는 시장 지표입니다.",
    supply: "코인이나 토큰이 현재 얼마나 유통되고 있고 공급량이 얼마나 빠르게 늘거나 줄어드는지 보여주는 지표입니다.",
    onchain: "블록체인에 직접 기록된 거래·주소·보유원가를 집계한 지표입니다.",
    protocol: "DeFi나 블록체인 서비스가 실제로 보유한 자산, 받은 수수료, 남긴 수익을 보여주는 지표입니다.",
    positioning: "투자자가 어느 방향에 얼마나 레버리지를 쌓았는지 보여주는 파생상품 포지션 지표입니다.",
  };
  return byCategory[item.category] || item.description_ko || `${item.name_ko}의 시계열 관측값입니다.`;
}

function initializeIndicatorCards() {
  document.querySelectorAll("[data-indicator-id]").forEach(card => {
    card.addEventListener("click", event => {
      if (event.target.closest("a, button, summary, details")) return;
      openIndicatorChart(card.dataset.indicatorId);
    });
    card.addEventListener("keydown", event => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        openIndicatorChart(card.dataset.indicatorId);
      }
    });
    card.querySelector(".indicator-chart-button")?.addEventListener("click", () => openIndicatorChart(card.dataset.indicatorId));
  });
}

function openIndicatorChart(indicatorId) {
  state.selectedIndicatorId = indicatorId;
  state.indicatorRange = 90;
  state.indicatorOffset = 0;
  state.indicatorHoverIndex = null;
  renderIndicatorModal();
  const modal = document.querySelector("#indicator-modal");
  modal.hidden = false;
  document.body.classList.add("modal-open");
}

function closeIndicatorChart() {
  document.querySelector("#indicator-modal").hidden = true;
  document.body.classList.remove("modal-open");
}

function renderIndicatorModal() {
  const item = state.pulse?.indicators?.find(row => row.indicator_id === state.selectedIndicatorId);
  if (!item) return;
  const learning = indicatorLearningGuide(item);
  document.querySelector("#indicator-modal-category").textContent = `${domainLabel(item.domain)} · ${categoryLabel(item.category)}`;
  document.querySelector("#indicator-modal-title").textContent = item.name_ko;
  document.querySelector("#indicator-modal-summary").textContent = item.impact_explanation || item.interpretation_ko || "";
  document.querySelector("#indicator-modal-value").innerHTML = `<span>현재값</span><strong>${formatIndicatorValue(item.value, item.unit)}</strong><em class="${item.tone || "neutral"}">${item.impact_label || "판단 보조"} ${item.impact_score ?? ""}</em>`;
  document.querySelectorAll("[data-indicator-range]").forEach(button => button.classList.toggle("active", Number(button.dataset.indicatorRange) === state.indicatorRange));
  const chart = document.querySelector("#indicator-modal-chart");
  chart.innerHTML = `<canvas id="indicator-series-chart" width="920" height="420" aria-label="${item.name_ko} 시계열 차트"></canvas><div class="indicator-chart-usage">휠 확대·축소 · 드래그 이동 · 더블클릭 초기화</div>`;
  initializeIndicatorSeriesInteractions();
  drawIndicatorSeriesChart();
  document.querySelector("#indicator-modal-guide").innerHTML = `<article><span>무엇인가</span><p>${learning.what}</p></article><article><span>현재 해석</span><strong class="${item.tone || "neutral"}">${item.impact_label || "판단 보조"}</strong><p>${item.level_label || ""} · 신뢰도 ${confidenceLabel(item.impact_confidence)}</p></article><article><span>왜 중요한가</span><p>${item.interpretation_ko || item.description_ko || ""}</p></article><article><span>예를 들면</span><p>${learning.example}</p></article><article><span>주의할 점</span><p>${learning.caveat}</p></article>`;
}

function visibleIndicatorSeries() {
  const item = state.pulse?.indicators?.find(row => row.indicator_id === state.selectedIndicatorId);
  const all = item?.series || [];
  const windowSize = Math.min(Math.max(10, state.indicatorRange), all.length);
  const offset = Math.min(Math.max(0, state.indicatorOffset), Math.max(all.length - windowSize, 0));
  const end = all.length - offset;
  return all.slice(Math.max(0, end - windowSize), end);
}

function drawIndicatorSeriesChart() {
  const canvas = document.querySelector("#indicator-series-chart");
  const item = state.pulse?.indicators?.find(row => row.indicator_id === state.selectedIndicatorId);
  if (!canvas || !item) return;
  const series = visibleIndicatorSeries();
  const cssWidth = canvas.clientWidth || 900;
  const cssHeight = canvas.clientHeight || 420;
  const ratio = window.devicePixelRatio || 1;
  canvas.width = Math.round(cssWidth * ratio);
  canvas.height = Math.round(cssHeight * ratio);
  const context = canvas.getContext("2d");
  context.setTransform(ratio, 0, 0, ratio, 0, 0);
  context.clearRect(0, 0, cssWidth, cssHeight);
  context.fillStyle = "#ffffff";
  context.fillRect(0, 0, cssWidth, cssHeight);
  if (!series.length) {
    context.fillStyle = "#75857f";
    context.font = "13px sans-serif";
    context.fillText("표시할 시계열이 없습니다", 24, 40);
    return;
  }

  const box = { left: 18, top: 18, right: cssWidth - 82, bottom: cssHeight - 38 };
  const values = series.map(row => Number(row.value));
  const rawMin = Math.min(...values), rawMax = Math.max(...values);
  const padding = Math.max((rawMax - rawMin) * 0.08, Math.abs(rawMax || 1) * 0.003);
  const min = rawMin - padding, max = rawMax + padding, span = max - min || 1;
  const xFor = index => box.left + (index / Math.max(series.length - 1, 1)) * (box.right - box.left);
  const yFor = value => box.top + (1 - (value - min) / span) * (box.bottom - box.top);

  context.lineWidth = 1;
  context.strokeStyle = "#e6ebe8";
  context.fillStyle = "#768781";
  context.font = "10px ui-monospace, SFMono-Regular, Menlo, monospace";
  for (let index = 0; index <= 5; index += 1) {
    const y = box.top + (index / 5) * (box.bottom - box.top);
    const value = max - (index / 5) * span;
    context.beginPath(); context.moveTo(box.left, y); context.lineTo(box.right, y); context.stroke();
    context.fillText(formatIndicatorValue(value, item.unit), box.right + 8, y + 3);
  }
  for (let index = 0; index <= 6; index += 1) {
    const x = box.left + (index / 6) * (box.right - box.left);
    context.beginPath(); context.moveTo(x, box.top); context.lineTo(x, box.bottom); context.stroke();
  }

  const color = item.tone === "negative" ? "#d96c4f" : item.tone === "neutral" ? "#a88d49" : "#2e8b6b";
  const gradient = context.createLinearGradient(0, box.top, 0, box.bottom);
  gradient.addColorStop(0, `${color}38`);
  gradient.addColorStop(1, `${color}05`);
  context.beginPath();
  series.forEach((row, index) => {
    const x = xFor(index), y = yFor(Number(row.value));
    if (index === 0) context.moveTo(x, y); else context.lineTo(x, y);
  });
  context.lineTo(box.right, box.bottom); context.lineTo(box.left, box.bottom); context.closePath();
  context.fillStyle = gradient; context.fill();
  context.beginPath();
  series.forEach((row, index) => {
    const x = xFor(index), y = yFor(Number(row.value));
    if (index === 0) context.moveTo(x, y); else context.lineTo(x, y);
  });
  context.strokeStyle = color; context.lineWidth = 2; context.stroke();

  const lastValue = Number(series[series.length - 1].value);
  const lastY = yFor(lastValue);
  context.setLineDash([4, 4]); context.strokeStyle = color; context.lineWidth = 1;
  context.beginPath(); context.moveTo(box.left, lastY); context.lineTo(box.right, lastY); context.stroke(); context.setLineDash([]);
  context.fillStyle = color; context.fillRect(box.right + 3, lastY - 10, 77, 20);
  context.fillStyle = "#fff"; context.font = "bold 9px ui-monospace, monospace";
  context.fillText(formatIndicatorValue(lastValue, item.unit).slice(0, 12), box.right + 7, lastY + 3);

  context.fillStyle = "#768781"; context.font = "10px ui-monospace, monospace";
  context.fillText(series[0].period, box.left, cssHeight - 13);
  const endLabel = series[series.length - 1].period;
  context.fillText(endLabel, box.right - context.measureText(endLabel).width, cssHeight - 13);

  const hoverIndex = state.indicatorHoverIndex;
  if (hoverIndex !== null && hoverIndex >= 0 && hoverIndex < series.length) {
    const row = series[hoverIndex], x = xFor(hoverIndex), y = yFor(Number(row.value));
    context.setLineDash([3, 3]); context.strokeStyle = "#6f7f79";
    context.beginPath(); context.moveTo(x, box.top); context.lineTo(x, box.bottom); context.stroke();
    context.beginPath(); context.moveTo(box.left, y); context.lineTo(box.right, y); context.stroke(); context.setLineDash([]);
    context.fillStyle = color; context.beginPath(); context.arc(x, y, 4, 0, Math.PI * 2); context.fill();
    const previous = hoverIndex > 0 ? Number(series[hoverIndex - 1].value) : null;
    const change = previous === null || previous === 0 ? null : (Number(row.value) - previous) / Math.abs(previous);
    const tooltip = `${row.period}  ${formatIndicatorValue(row.value, item.unit)}${change === null ? "" : `  ${change >= 0 ? "+" : ""}${(change * 100).toFixed(2)}%`}`;
    context.font = "bold 11px sans-serif";
    const tooltipWidth = context.measureText(tooltip).width + 20;
    const tooltipX = Math.min(Math.max(box.left, x - tooltipWidth / 2), box.right - tooltipWidth);
    context.fillStyle = "rgba(16,41,37,0.92)"; context.fillRect(tooltipX, box.top + 8, tooltipWidth, 28);
    context.fillStyle = "#fff"; context.fillText(tooltip, tooltipX + 10, box.top + 26);
  }
}

function initializeIndicatorSeriesInteractions() {
  const canvas = document.querySelector("#indicator-series-chart");
  if (!canvas) return;
  canvas.addEventListener("wheel", event => {
    event.preventDefault();
    const item = state.pulse?.indicators?.find(row => row.indicator_id === state.selectedIndicatorId);
    const total = item?.series?.length || 0;
    state.indicatorRange = Math.min(total, Math.max(10, Math.round(state.indicatorRange * (event.deltaY > 0 ? 1.18 : 0.84))));
    state.indicatorOffset = Math.min(state.indicatorOffset, Math.max(total - state.indicatorRange, 0));
    state.indicatorHoverIndex = null;
    drawIndicatorSeriesChart();
  }, { passive: false });
  canvas.addEventListener("pointerdown", event => {
    canvas.setPointerCapture?.(event.pointerId);
    state.indicatorDrag = { pointerId: event.pointerId, startX: event.clientX, startOffset: state.indicatorOffset };
  });
  canvas.addEventListener("pointermove", event => {
    const series = visibleIndicatorSeries();
    const rect = canvas.getBoundingClientRect();
    if (state.indicatorDrag?.pointerId === event.pointerId) {
      const item = state.pulse?.indicators?.find(row => row.indicator_id === state.selectedIndicatorId);
      const total = item?.series?.length || 0;
      const delta = Math.round((event.clientX - state.indicatorDrag.startX) / Math.max(rect.width, 1) * state.indicatorRange);
      state.indicatorOffset = Math.min(Math.max(0, state.indicatorDrag.startOffset + delta), Math.max(total - state.indicatorRange, 0));
    } else {
      const ratio = Math.min(1, Math.max(0, (event.clientX - rect.left - 18) / Math.max(rect.width - 100, 1)));
      state.indicatorHoverIndex = Math.round(ratio * Math.max(series.length - 1, 0));
    }
    drawIndicatorSeriesChart();
  });
  const release = event => { if (state.indicatorDrag?.pointerId === event.pointerId) state.indicatorDrag = null; };
  canvas.addEventListener("pointerup", release);
  canvas.addEventListener("pointercancel", release);
  canvas.addEventListener("pointerleave", () => { if (!state.indicatorDrag) { state.indicatorHoverIndex = null; drawIndicatorSeriesChart(); } });
  canvas.addEventListener("dblclick", () => {
    state.indicatorRange = Math.min(90, state.pulse?.indicators?.find(row => row.indicator_id === state.selectedIndicatorId)?.series?.length || 90);
    state.indicatorOffset = 0; state.indicatorHoverIndex = null; drawIndicatorSeriesChart();
  });
}

function sparkline(series, tone) {
  if (!series || series.length < 2) return `<div class="sparkline-placeholder"></div>`;
  const values = series.map(item => Number(item.value));
  const low = Math.min(...values);
  const high = Math.max(...values);
  const range = high - low || 1;
  const points = values.map((value, index) => {
    const x = (index / Math.max(values.length - 1, 1)) * 100;
    const y = 29 - ((value - low) / range) * 25;
    return `${x.toFixed(2)},${y.toFixed(2)}`;
  }).join(" ");
  return `<svg class="sparkline ${tone}" viewBox="0 0 100 32" preserveAspectRatio="none" aria-hidden="true"><polyline points="${points}" /></svg>`;
}

function renderConnectionList(unavailable) {
  const container = document.querySelector("#connection-list");
  const priority = [...unavailable]
    .sort((a, b) => Number(b.importance || 0) - Number(a.importance || 0))
    .slice(0, 5);
  container.innerHTML = priority.length
    ? priority.map(item => `<div><span class="connection-dot ${item.availability}"></span><strong>${item.name_ko}</strong><small>${availabilityLabel(item.availability)}</small></div>`).join("")
    : `<div><span class="connection-dot public"></span><strong>모든 공급자 정상</strong><small>최신 상태</small></div>`;
}

function formatIndicatorValue(value, unit) {
  const number = Number(value);
  if (!Number.isFinite(number)) return "-";
  if (unit === "%" || unit === "%p" || unit === "ratio") return `${number.toFixed(Math.abs(number) < 10 ? 2 : 1)}${unit === "ratio" ? "×" : unit}`;
  if (unit === "USD" && Math.abs(number) >= 1e12) return `$${(number / 1e12).toFixed(2)}T`;
  if (unit === "USD" && Math.abs(number) >= 1e9) return `$${(number / 1e9).toFixed(1)}B`;
  if ((unit === "count" || unit === "TH/s") && Math.abs(number) >= 1e6) return `${(number / 1e6).toFixed(1)}M`;
  if (unit === "USD bn") return `$${new Intl.NumberFormat("ko-KR", { maximumFractionDigits: 1 }).format(number)}B`;
  return `${new Intl.NumberFormat("ko-KR", { maximumFractionDigits: 2 }).format(number)}${unit && unit !== "index" ? ` ${unit}` : ""}`;
}

const domainLabel = domain => ({ global: "글로벌", korea: "한국", equity: "주식", crypto: "코인" })[domain] || domain || "시장";
const categoryLabel = category => ({ rates: "금리", liquidity: "유동성", inflation: "물가", growth: "경기", labor: "고용", credit: "신용", fx: "환율", commodities: "원자재", onchain: "온체인", network: "네트워크", protocol: "프로토콜", supply: "공급", flows: "자금흐름", market: "시장", positioning: "포지셔닝", earnings: "실적", crypto_liquidity: "코인 유동성" })[category] || category || "지표";
const availabilityLabel = value => ({ public: "공개 데이터", derived: "파생 계산", api_key: "API 키 필요", licensed: "계약 데이터 필요" })[value] || "수집 대기";
const freshnessLabel = (freshness, availability) => freshness === "fresh" ? "최신" : freshness === "aging" ? "갱신 대기" : freshness === "stale" ? "오래됨" : availabilityLabel(availability);
const confidenceLabel = value => ({ high: "높음", medium: "보통", low: "낮음", none: "없음" })[value] || "보통";

function setActiveTab(tabName) {
  state.activeTab = tabName;
  document.querySelectorAll("[data-tab]").forEach(button => {
    const active = button.dataset.tab === tabName;
    button.classList.toggle("active", active);
    button.setAttribute("aria-selected", String(active));
  });
  document.querySelectorAll("[data-panel]").forEach(panel => {
    const active = panel.dataset.panel === tabName;
    panel.classList.toggle("active", active);
    panel.hidden = !active;
  });
  const sidebarFilters = document.querySelector(".filters");
  if (sidebarFilters) sidebarFilters.hidden = tabName !== "signals";
  if (tabName === "paper") {
    loadPaperDashboard();
  }
}

async function loadPaperDashboard() {
  const response = await fetch("/api/paper");
  state.paper = await response.json();
  renderPaperDashboard();
}

function renderPaperDashboard() {
  const paper = state.paper || { accounts: [], strategies: [], backtests: [] };
  renderPaperSelect("#paper-account-select", paper.accounts || [], "account_id", "name");
  renderPaperSelect("#paper-strategy-select", paper.strategies || [], "strategy_id", "name");
  updatePaperProgress(paper);
  renderLatestBacktest((paper.backtests || [])[0]);
  renderStrategyRuleSummary();
  renderPaperList(
    "#paper-accounts",
    paper.accounts || [],
    account => account.name,
    account => `${account.base_currency || "KRW"} ${formatNumber(account.initial_cash)}`,
    "아직 가상 계좌가 없습니다",
    "1단계에서 실험 자본을 만들어 주세요."
  );
  renderPaperList(
    "#paper-strategies",
    paper.strategies || [],
    strategy => strategy.name,
    strategy => strategySummary(strategy),
    "아직 전략이 없습니다",
    "프리셋을 선택하고 전략을 저장해 주세요."
  );
  renderPaperList(
    "#paper-backtests",
    paper.backtests || [],
    run => `${formatPercent(run.total_return)} 수익률`,
    run => `${run.start_date || "-"} ~ ${run.end_date || "-"} · ${formatNumber(run.trade_count)}회 거래`,
    "아직 백테스트가 없습니다",
    "계좌와 전략을 선택해 첫 실험을 실행해 주세요."
  );
}

function renderPaperSelect(selector, rows, valueKey, labelKey) {
  const select = document.querySelector(selector);
  if (!select) return;
  select.innerHTML = rows.length
    ? rows.map(row => `<option value="${row[valueKey]}">${row[labelKey] || row[valueKey]}</option>`).join("")
    : `<option value="">먼저 이전 단계를 완료하세요</option>`;
  select.disabled = !rows.length;
}

function renderPaperList(selector, rows, title, detail, emptyTitle, emptyDetail) {
  const container = document.querySelector(selector);
  if (!container) return;
  container.innerHTML = rows.length
    ? rows.map(row => `<article><strong>${title(row)}</strong><span>${detail(row)}</span></article>`).join("")
    : `<article class="paper-empty"><strong>${emptyTitle}</strong><span>${emptyDetail}</span></article>`;
}

function strategySummary(strategy) {
  const config = parseJson(strategy.config_json) || {};
  const patterns = (config.pattern_names || []).map(humanPatternName).join(", ") || "전체 패턴";
  const indicators = (config.selected_indicators || []).join(", ") || "all indicators";
  return `${patterns} · 근거 ${config.min_indicator_agreement || 0}개 이상 · ${indicators}`;
}

function updatePaperProgress(paper) {
  const states = {
    account: (paper.accounts || []).length > 0,
    strategy: (paper.strategies || []).length > 0,
    backtest: (paper.backtests || []).length > 0,
  };
  document.querySelectorAll("[data-paper-step]").forEach(step => {
    const key = step.dataset.paperStep;
    const complete = states[key];
    const priorComplete = key === "account" || (key === "strategy" ? states.account : states.account && states.strategy);
    step.classList.toggle("complete", complete);
    step.classList.toggle("current", !complete && priorComplete);
    step.classList.toggle("locked", !complete && !priorComplete);
    step.querySelector("em").textContent = complete ? "완료" : priorComplete ? "진행" : "잠김";
  });
  document.querySelector("#paper-account-status").textContent = states.account ? `${paper.accounts.length}개 준비됨` : "필수";
  document.querySelector("#paper-strategy-status").textContent = states.strategy ? `${paper.strategies.length}개 준비됨` : states.account ? "진행 가능" : "계좌 필요";
  document.querySelector("#paper-backtest-status").textContent = states.backtest ? `${paper.backtests.length}회 완료` : states.strategy ? "실행 가능" : "전략 필요";
  document.querySelector("#paper-step-strategy").classList.toggle("step-locked", !states.account);
  document.querySelector("#paper-step-backtest").classList.toggle("step-locked", !states.strategy);
  document.querySelectorAll("[data-strategy-preset]").forEach(button => { button.disabled = !states.account; });
  const strategySubmit = document.querySelector('#paper-strategy-form button[type="submit"]');
  if (strategySubmit) strategySubmit.disabled = !states.account;
  const backtestSubmit = document.querySelector('#paper-backtest-form button[type="submit"]');
  if (backtestSubmit) backtestSubmit.disabled = !(states.account && states.strategy);
}

function renderLatestBacktest(run) {
  const container = document.querySelector("#paper-latest-result");
  if (!run) {
    container.className = "paper-latest-result empty";
    container.innerHTML = `<div><span class="section-kicker">LATEST RESULT</span><h2>아직 백테스트 결과가 없습니다</h2><p>아래 3단계를 완료하면 수익률·최대낙폭·승률을 해석해 드립니다.</p></div>`;
    return;
  }
  const totalReturn = Number(run.total_return || 0);
  const drawdown = Math.abs(Number(run.max_drawdown || 0));
  const trades = Number(run.trade_count || 0);
  const promising = totalReturn > 0 && drawdown <= 0.2 && trades >= 20;
  const dangerous = totalReturn <= 0 || drawdown >= 0.3;
  const verdict = promising ? "다음 검증 가치 있음" : dangerous ? "규칙 수정 필요" : "표본 추가 필요";
  const tone = promising ? "positive" : dangerous ? "negative" : "neutral";
  const explanation = promising
    ? "수익이 양수이고 최대낙폭이 통제됐습니다. 다른 기간에서도 재검증하세요."
    : dangerous
      ? "수익 또는 낙폭이 기준을 벗어났습니다. 진입 조건과 포지션 크기를 조정하세요."
      : "거래 수나 위험 대비 수익이 아직 충분하지 않습니다.";
  container.className = `paper-latest-result ${tone}`;
  container.innerHTML = `<div class="latest-verdict"><span class="section-kicker">LATEST RESULT</span><h2>${verdict}</h2><p>${explanation}</p></div>
    <div class="latest-result-metrics"><div><span>총수익률</span><strong>${formatPercent(totalReturn)}</strong></div><div><span>최대낙폭</span><strong>${formatPercent(drawdown)}</strong></div><div><span>승률</span><strong>${formatPercent(run.win_rate)}</strong></div><div><span>거래 수</span><strong>${formatNumber(trades)}</strong></div></div>`;
}

const STRATEGY_PRESETS = {
  conservative: { name: "보수적 리테스트 전략", patterns: ["inverse_head_and_shoulders", "double_bottom"], indicators: ["volume_profile", "vwap", "ema", "htf_trend"], votes: "4", score: "0.80", position: "0.10", maxPositions: "4", hold: "20", stopLoss: "0.06", takeProfit: "0.15", retest: true },
  balanced: { name: "균형형 패턴 전략", patterns: ["inverse_head_and_shoulders", "double_bottom", "ascending_triangle", "falling_wedge"], indicators: ["volume_profile", "vwap", "ema"], votes: "3", score: "0.70", position: "0.20", maxPositions: "5", hold: "20", stopLoss: "0.08", takeProfit: "0.20", retest: false },
  aggressive: { name: "공격적 모멘텀 전략", patterns: ["double_bottom", "ascending_triangle", "falling_wedge", "symmetrical_triangle"], indicators: ["vwap", "ema", "volume"], votes: "2", score: "0.60", position: "0.30", maxPositions: "5", hold: "10", stopLoss: "0.10", takeProfit: "0.25", retest: false },
};

function applyStrategyPreset(presetName) {
  const preset = STRATEGY_PRESETS[presetName];
  const form = document.querySelector("#paper-strategy-form");
  if (!preset || !form) return;
  form.elements.name.value = preset.name;
  form.querySelectorAll('[name="pattern_names"]').forEach(input => { input.checked = preset.patterns.includes(input.value); });
  form.querySelectorAll('[name="selected_indicators"]').forEach(input => { input.checked = preset.indicators.includes(input.value); });
  form.elements.min_indicator_agreement.value = preset.votes;
  form.elements.min_confluence.value = preset.score;
  form.elements.position_pct.value = preset.position;
  form.elements.max_positions.value = preset.maxPositions;
  form.elements.hold_days.value = preset.hold;
  form.elements.stop_loss_pct.value = preset.stopLoss;
  form.elements.take_profit_pct.value = preset.takeProfit;
  form.elements.require_retest.checked = preset.retest;
  document.querySelectorAll("[data-strategy-preset]").forEach(button => button.classList.toggle("active", button.dataset.strategyPreset === presetName));
  renderStrategyRuleSummary();
}

function renderStrategyRuleSummary() {
  const form = document.querySelector("#paper-strategy-form");
  const container = document.querySelector("#strategy-rule-summary");
  if (!form || !container) return;
  const patterns = [...form.querySelectorAll('[name="pattern_names"]')].filter(input => input.checked);
  const indicators = [...form.querySelectorAll('[name="selected_indicators"]')].filter(input => input.checked);
  const votes = Number(form.elements.min_indicator_agreement.value || 0);
  const score = Math.round(Number(form.elements.min_confluence.value || 0) * 100);
  const position = Math.round(Number(form.elements.position_pct.value || 0) * 100);
  const maxPositions = Number(form.elements.max_positions.value || 0);
  const hold = Number(form.elements.hold_days.value || 0);
  const stop = Math.round(Number(form.elements.stop_loss_pct.value || 0) * 100);
  const take = Math.round(Number(form.elements.take_profit_pct.value || 0) * 100);
  const impossible = votes > indicators.length;
  container.classList.toggle("warning", impossible || !patterns.length || !indicators.length);
  const trigger = patterns.length ? patterns.map(input => humanPatternName(input.value)).join(", ") : "선택된 패턴 없음";
  const confirmation = indicators.length ? `${indicators.length}개 근거 중 ${votes}개 이상 상승 일치` : "확인 근거 없음";
  container.innerHTML = `<div><span>이 전략은 이렇게 움직입니다</span><strong>${trigger}</strong></div>
    <ol><li>패턴 점수 <b>${score}점 이상</b>${form.elements.require_retest.checked ? "이며 리테스트까지 확인" : ""}</li><li>${confirmation} 시 <b>신호일 종가</b>에 진입</li><li>남은 현금의 <b>${position}%</b>씩, 최대 <b>${maxPositions}종목</b> 보유</li><li><b>-${stop}% 손절</b>, <b>+${take}% 익절</b>, 또는 <b>${hold}거래일</b> 경과 시 매도</li></ol>
    ${impossible ? `<p>최소 근거 수가 선택한 근거보다 많아 진입 신호가 발생할 수 없습니다.</p>` : ""}`;
}

async function usePatternInExperiment(patternName) {
  setActiveTab("paper");
  await loadPaperDashboard();
  applyStrategyPreset("balanced");
  const form = document.querySelector("#paper-strategy-form");
  form.querySelectorAll('[name="pattern_names"]').forEach(input => { input.checked = input.value === patternName; });
  form.elements.name.value = `${humanPatternName(patternName)} 검증 전략`;
  renderStrategyRuleSummary();
  document.querySelector("#paper-step-strategy")?.scrollIntoView({ behavior: "smooth", block: "start" });
}

function parseJson(value) {
  if (!value) return null;
  try {
    return typeof value === "string" ? JSON.parse(value) : value;
  } catch {
    return null;
  }
}

function renderSnapshot(data) {
  const topSignal = (data.signals || [])[0];
  const bestPattern = (data.performance || [])[0];
  const universe = (data.universe || [])[0];
  document.querySelector("#snapshot-top-signal").textContent = topSignal
    ? `${topSignal.symbol} ${topSignal.pattern_name} ${Number(topSignal.confluence_score || 0).toFixed(2)}`
    : "-";
  document.querySelector("#snapshot-best-pattern").textContent = bestPattern
    ? `${bestPattern.pattern_name} ${bestPattern.horizon_days}D ${formatPercent(bestPattern.target_hit_rate)}`
    : "-";
  document.querySelector("#snapshot-universe").textContent = universe ? universe.universe_name : "-";
}

function filteredSignals(signals) {
  const pattern = document.querySelector("#pattern-filter").value;
  const symbol = document.querySelector("#symbol-filter").value.trim();
  const retestOnly = document.querySelector("#retest-filter").checked;
  return signals.filter(signal => {
    if (pattern && signal.pattern_name !== pattern) return false;
    if (symbol && !signal.symbol.includes(symbol)) return false;
    if (retestOnly && !signal.retest_confirmed) return false;
    return true;
  });
}

function renderSignals(signals) {
  const rows = filteredSignals(signals)
    .map(
      signal => `<tr>
        <td>${signal.symbol}</td>
        <td>${signal.name || signal.symbol}</td>
        <td>${signal.pattern_name}</td>
        <td>${signal.detection_date}</td>
        <td>${Number(signal.confluence_score || 0).toFixed(2)}</td>
        <td>${signal.htf_trend}</td>
        <td>${tag(signal.volume_profile_confluence)}</td>
        <td>${tag(signal.retest_confirmed)}</td>
      </tr>`
    )
    .join("");
  document.querySelector("#signals-body").innerHTML = rows || `<tr><td colspan="8">No signals found</td></tr>`;
}

function renderPerformance(performance) {
  const qualified = performance.filter(item => patternVerdict(item).key === "qualified");
  const risky = performance.filter(item => Number(item.avg_return_at_horizon || 0) <= 0);
  const best = [...qualified].sort((a, b) => patternEvidenceScore(b) - patternEvidenceScore(a))[0]
    || [...performance].sort((a, b) => patternEvidenceScore(b) - patternEvidenceScore(a))[0];
  document.querySelector("#pattern-qualified-count").textContent = formatNumber(qualified.length);
  document.querySelector("#pattern-best-name").textContent = best ? humanPatternName(best.pattern_name) : "-";
  document.querySelector("#pattern-best-detail").textContent = best
    ? `${best.horizon_days}일 · ${formatPercent(best.avg_return_at_horizon)} 평균수익`
    : "표본 확인 중";
  document.querySelector("#pattern-total-samples").textContent = formatNumber(
    performance.reduce((total, item) => total + Number(item.sample_count || 0), 0)
  );
  document.querySelector("#pattern-risk-count").textContent = formatNumber(risky.length);

  const horizon = document.querySelector("#pattern-horizon-filter")?.value || "all";
  const minSamples = Number(document.querySelector("#pattern-sample-filter")?.value || 0);
  const verdictFilter = document.querySelector("#pattern-verdict-filter")?.value || "all";
  const filtered = performance.filter(item => {
    const verdict = patternVerdict(item);
    if (horizon !== "all" && Number(item.horizon_days) !== Number(horizon)) return false;
    if (Number(item.sample_count || 0) < minSamples) return false;
    if (verdictFilter !== "all" && verdict.key !== verdictFilter) return false;
    return true;
  });

  const bestByPattern = new Map();
  filtered.forEach(item => {
    const current = bestByPattern.get(item.pattern_name);
    if (!current || patternEvidenceScore(item) > patternEvidenceScore(current)) bestByPattern.set(item.pattern_name, item);
  });
  const cards = [...bestByPattern.values()]
    .sort((a, b) => patternEvidenceScore(b) - patternEvidenceScore(a))
    .slice(0, 12)
    .map(item => {
      const verdict = patternVerdict(item);
      const variants = filtered.filter(row => row.pattern_name === item.pattern_name).length;
      return `<article class="pattern-evidence-card ${verdict.tone}">
        <div class="pattern-evidence-head"><span class="verdict-badge ${verdict.tone}">${verdict.label}</span><small>${item.horizon_days}일 보유</small></div>
        <h3>${humanPatternName(item.pattern_name)}</h3>
        <p>${patternVerdictReason(item, verdict)}</p>
        <div class="pattern-stat-row"><div><span>표본</span><strong>${formatNumber(item.sample_count)}</strong></div><div><span>적중률</span><strong>${formatPercent(item.target_hit_rate)}</strong></div><div><span>평균수익</span><strong class="${Number(item.avg_return_at_horizon) > 0 ? "positive" : "negative"}">${formatPercent(item.avg_return_at_horizon)}</strong></div></div>
        <footer><span>최적 조건: ${item.htf_trend || "unknown"} 추세 · ${item.retest_confirmed ? "리테스트 확인" : "리테스트 없음"} · ${variants}개 조합</span><button type="button" data-pattern-experiment="${item.pattern_name}">전략으로 실험</button></footer>
      </article>`;
    })
    .join("");
  document.querySelector("#pattern-card-grid").innerHTML = cards
    || `<article class="analysis-empty"><strong>조건을 충족하는 조합이 없습니다</strong><p>최소 표본이나 판정 필터를 완화해 보세요.</p></article>`;

  const rows = filtered
    .map(
      item => {
        const verdict = patternVerdict(item);
        return `<tr>
        <td><span class="verdict-badge ${verdict.tone}">${verdict.label}</span></td>
        <td>${humanPatternName(item.pattern_name)}</td>
        <td>${item.horizon_days}D</td>
        <td>${item.htf_trend}</td>
        <td>${tag(item.retest_confirmed)}</td>
        <td>${formatNumber(item.sample_count)}</td>
        <td>${formatPercent(item.target_hit_rate)}</td>
        <td>${formatPercent(item.avg_return_at_horizon)}</td>
        <td>${Number(item.avg_confluence_score || 0).toFixed(2)}</td>
      </tr>`;
      }
    )
    .join("");
  document.querySelector("#performance-body").innerHTML = rows || `<tr><td colspan="9">조건에 맞는 근거가 없습니다</td></tr>`;
  document.querySelectorAll("[data-pattern-experiment]").forEach(button => {
    button.addEventListener("click", () => usePatternInExperiment(button.dataset.patternExperiment));
  });
}

function patternVerdict(item) {
  const samples = Number(item.sample_count || 0);
  const hitRate = Number(item.target_hit_rate || 0);
  const averageReturn = Number(item.avg_return_at_horizon || 0);
  if (samples >= 30 && hitRate >= 0.55 && averageReturn > 0) return { key: "qualified", label: "검증 통과", tone: "positive" };
  if (samples >= 20 && averageReturn > 0) return { key: "watch", label: "관찰 가치", tone: "neutral" };
  return { key: "weak", label: samples < 20 ? "표본 부족" : "근거 약함", tone: "negative" };
}

function patternEvidenceScore(item) {
  const sampleWeight = Math.min(1, Number(item.sample_count || 0) / 50);
  return Number(item.avg_return_at_horizon || 0) * 100 + Number(item.target_hit_rate || 0) * 2 + sampleWeight;
}

function patternVerdictReason(item, verdict) {
  if (verdict.key === "qualified") return "표본·적중률·평균수익이 모두 기본 검증선을 넘었습니다.";
  if (verdict.key === "watch") return "수익 근거는 있지만 표본이나 적중률을 더 확인해야 합니다.";
  if (Number(item.sample_count || 0) < 20) return "표본이 적어 현재 수치를 전략 근거로 쓰기 어렵습니다.";
  return "평균수익 또는 적중률이 기준에 못 미쳐 단독 사용은 위험합니다.";
}

function humanPatternName(value) {
  return String(value || "unknown").split("_").map(word => word.charAt(0).toUpperCase() + word.slice(1)).join(" ");
}

function technicalBySymbol(technicals) {
  return new Map(technicals.map(item => [item.symbol, item]));
}

function sentimentBySymbol(sentiment) {
  return new Map(sentiment.map(item => [item.symbol, item]));
}

function formatDecimal(value) {
  if (value === null || value === undefined || value === "") {
    return "-";
  }
  return Number(value).toLocaleString("en-US", { maximumFractionDigits: 2 });
}

function renderUniverse(universe, technicals, sentiment, signals) {
  const features = technicalBySymbol(technicals);
  const sentimentRows = sentimentBySymbol(sentiment);
  const signalRows = new Map();
  signals.forEach(signal => {
    if (!signalRows.has(signal.symbol)) signalRows.set(signal.symbol, signal);
  });
  const enriched = universe.map(item => {
      const technical = features.get(item.symbol) || {};
      const sentimentItem = sentimentRows.get(item.symbol) || {};
      const signal = signalRows.get(item.symbol);
      const ema20 = Number(technical.ema_20);
      const ema50 = Number(technical.ema_50);
      const rsi = technical.rsi_14 === null || technical.rsi_14 === undefined ? null : Number(technical.rsi_14);
      const trend = Number.isFinite(ema20) && Number.isFinite(ema50) ? (ema20 > ema50 ? "bullish" : ema20 < ema50 ? "bearish" : "neutral") : "neutral";
      const sentimentLabel = sentimentItem.sentiment_label || "neutral";
      const reasons = [];
      let priority = 0;
      if (signal) { priority += 35; reasons.push(`${humanPatternName(signal.pattern_name)} 시그널`); }
      if (trend === "bullish") { priority += 20; reasons.push("EMA 상승 추세"); }
      if (trend === "bearish") { priority -= 20; reasons.push("EMA 하락 추세"); }
      if (technical.rsi_divergence === "bullish") { priority += 18; reasons.push("RSI 상승 다이버전스"); }
      if (technical.rsi_divergence === "bearish") { priority -= 18; reasons.push("RSI 하락 다이버전스"); }
      if (rsi !== null && rsi <= 35) { priority += 8; reasons.push(`RSI ${rsi.toFixed(1)} 과매도권`); }
      if (rsi !== null && rsi >= 70) { priority -= 30; reasons.push(`RSI ${rsi.toFixed(1)} 과열 위험`); }
      if (sentimentLabel === "bullish") { priority += 10; reasons.push("긍정 뉴스 흐름"); }
      if (sentimentLabel === "bearish") { priority -= 15; reasons.push("부정 뉴스 흐름"); }
      const hardRisk = (rsi !== null && rsi >= 75) || technical.rsi_divergence === "bearish" || sentimentLabel === "bearish";
      const category = hardRisk || trend === "bearish" ? "risk" : signal ? "signal" : trend === "bullish" ? "momentum" : rsi !== null && rsi <= 35 ? "oversold" : "all";
      return { ...item, technical, sentiment: sentimentItem, signal, rsi, trend, priority, reasons, category, hardRisk };
    });

  document.querySelector("#explorer-signal-count").textContent = formatNumber(enriched.filter(item => item.signal).length);
  document.querySelector("#explorer-trend-count").textContent = formatNumber(enriched.filter(item => item.trend === "bullish").length);
  document.querySelector("#explorer-oversold-count").textContent = formatNumber(enriched.filter(item => item.rsi !== null && item.rsi <= 35).length);
  document.querySelector("#explorer-risk-count").textContent = formatNumber(enriched.filter(item => item.trend === "bearish" || item.hardRisk).length);

  const search = (document.querySelector("#universe-search")?.value || "").trim().toLowerCase();
  const filter = document.querySelector("#universe-filter")?.value || "all";
  const sort = document.querySelector("#universe-sort")?.value || "priority";
  const filtered = enriched.filter(item => {
    if (search && !`${item.symbol} ${item.name || ""}`.toLowerCase().includes(search)) return false;
    if (filter === "signal" && !item.signal) return false;
    if (filter === "momentum" && item.trend !== "bullish") return false;
    if (filter === "oversold" && !(item.rsi !== null && item.rsi <= 35)) return false;
    if (filter === "risk" && !(item.trend === "bearish" || item.hardRisk)) return false;
    return true;
  });
  filtered.sort((left, right) => {
    if (sort === "rank") return Number(left.rank || 999) - Number(right.rank || 999);
    if (sort === "rsi_low") return Number(left.rsi ?? 999) - Number(right.rsi ?? 999);
    if (sort === "rsi_high") return Number(right.rsi ?? -1) - Number(left.rsi ?? -1);
    return right.priority - left.priority || Number(left.rank || 999) - Number(right.rank || 999);
  });

  document.querySelector("#universe-watchlist").innerHTML = filtered.length
    ? filtered.slice(0, 6).map(item => `<article class="watchlist-card" data-symbol="${item.symbol}">
        <div><span>#${item.rank} · ${item.symbol}</span><strong>${item.name || item.symbol}</strong></div>
        <span class="priority-badge ${priorityTone(item.priority, item.hardRisk)}">${priorityLabel(item.priority, item.hardRisk)}</span>
        <p>${item.reasons.slice(0, 3).join(" · ") || "추가 근거를 기다리는 중"}</p>
        <footer><span class="trend ${item.trend}">${trendLabel(item.trend)}</span><span>RSI ${item.rsi === null ? "-" : item.rsi.toFixed(1)}</span><button type="button">차트 보기</button></footer>
      </article>`).join("")
    : `<article class="analysis-empty"><strong>조건에 맞는 종목이 없습니다</strong><p>검색어나 관찰 목적을 바꿔 보세요.</p></article>`;

  const rows = filtered.map(item => `<tr data-symbol="${item.symbol}">
        <td>${item.rank}</td>
        <td><strong>${item.name || item.symbol}</strong><small>${item.symbol}</small></td>
        <td><span class="priority-badge ${priorityTone(item.priority, item.hardRisk)}">${priorityLabel(item.priority, item.hardRisk)}</span></td>
        <td class="reason-cell" title="${item.reasons.join(" · ")}">${item.reasons.slice(0, 3).join(" · ") || "추가 근거 대기"}</td>
        <td><span class="trend ${item.trend}">${trendLabel(item.trend)}</span></td>
        <td>${item.rsi === null ? "-" : item.rsi.toFixed(1)}</td>
        <td><span class="sentiment ${item.sentiment.sentiment_label || "neutral"}">${sentimentKorean(item.sentiment.sentiment_label)}</span></td>
        <td>${item.signal ? `${humanPatternName(item.signal.pattern_name)} · ${Number(item.signal.confluence_score || 0).toFixed(2)}` : "-"}</td>
      </tr>`).join("");
  document.querySelector("#universe-body").innerHTML = rows || `<tr><td colspan="8">조건에 맞는 종목이 없습니다</td></tr>`;
  document.querySelectorAll("#universe-body [data-symbol], #universe-watchlist [data-symbol]").forEach(row => {
    row.addEventListener("click", event => {
      if (event.target.closest("a")) return;
      setActiveTab("overview");
      loadSymbolChart(row.dataset.symbol);
    });
  });
}

const priorityLabel = (score, hardRisk = false) => hardRisk ? "위험 확인" : score >= 45 ? "우선 검토" : score >= 15 ? "관찰" : score <= -20 ? "주의" : "근거 대기";
const priorityTone = (score, hardRisk = false) => hardRisk ? "negative" : score >= 45 ? "positive" : score >= 15 ? "neutral" : score <= -20 ? "negative" : "muted";
const trendLabel = value => ({ bullish: "상승 추세", bearish: "하락 추세", neutral: "추세 중립" })[value] || "추세 중립";
const sentimentKorean = value => ({ bullish: "긍정", bearish: "부정", neutral: "중립" })[value] || "중립";

function updateChartSymbolOptions(universe) {
  const select = document.querySelector("#chart-symbol");
  if (!select) return;
  const current = state.selectedSymbol || select.value;
  const countries = [...new Set(universe.map(item => item.market_country || "KR"))];
  select.innerHTML = countries.map(country => {
    const flag = country === "US" ? "🇺🇸" : country === "KR" ? "🇰🇷" : "🌐";
    const options = universe
      .filter(item => (item.market_country || "KR") === country)
      .map(item => `<option value="${item.symbol}">${flag} ${item.symbol} ${item.name || ""}</option>`)
      .join("");
    return `<optgroup label="${flag} ${country}">${options}</optgroup>`;
  }).join("");
  const next = universe.some(item => item.symbol === current) ? current : universe[0]?.symbol;
  if (!next) {
    return;
  }
  select.value = next;
  if (state.selectedSymbol !== next || !state.chart) {
    loadSymbolChart(next);
  }
}

async function loadSymbolChart(symbol) {
  if (!symbol) return;
  state.selectedSymbol = symbol;
  const select = document.querySelector("#chart-symbol");
  if (select && select.value !== symbol) {
    select.value = symbol;
  }
  const response = await fetch(`/api/symbols/${encodeURIComponent(symbol)}/chart`);
  state.chart = await response.json();
  state.chartOffset = 0;
  state.chartDrag = null;
  state.enabledPatternTypes = new Set((state.chart.patterns || []).map(pattern => pattern.pattern_name));
  state.selectedPatternEventKey = "";
  renderCandlestickChart(state.chart);
}

function renderCandlestickChart(chart) {
  const canvas = document.querySelector("#symbol-chart");
  const legend = document.querySelector("#chart-legend");
  if (!canvas || !legend) return;
  const patternFocus = document.querySelector(".pattern-focus");
  if (patternFocus) patternFocus.hidden = !state.indicators.patterns;
  updateChartYScaleControl();
  const candles = chart?.candles || [];
  if (!candles.length) {
    clearChart(canvas);
    legend.textContent = "No chart data";
    renderSignalEvidence(chart, []);
    return;
  }
  const visibleCandles = visibleChartCandles(candles);
  const visibleDates = new Set(visibleCandles.map(candle => candle.trade_date));
  const visiblePatterns = selectedVisiblePatterns(chart.patterns || [], visibleDates);
  const visibleCandidates = selectedVisibleCandidates(chart.pattern_candidates || [], visibleDates);
  renderPatternControls(chart.patterns || [], chart.pattern_candidates || [], visiblePatterns);

  const ctx = prepareCanvas(canvas);
  const width = canvas.clientWidth;
  const height = canvas.clientHeight;
  const priceBox = { left: 56, top: 22, right: width - 18, bottom: Math.round(height * 0.64) };
  const plotBox = { ...priceBox, right: state.indicators.profile ? width - 150 : priceBox.right };
  const volumeBox = { left: 56, top: priceBox.bottom + 18, right: width - 18, bottom: Math.round(height * 0.81) };
  const rsiBox = { left: 56, top: volumeBox.bottom + 22, right: width - 18, bottom: height - 24 };
  const profile = state.indicators.profile ? (chart.volume_profile || [])[0] || {} : {};
  const patternLevels = visiblePatterns.flatMap(pattern => [
    pattern.neckline_price,
    pattern.support_price,
    pattern.resistance_price,
    pattern.target_price,
    ...geometryPrices(pattern),
  ]);
  const candidateLevels = visibleCandidates.flatMap(candidate => [
    candidate.trigger_price,
    candidate.invalidation_price,
    ...geometryPrices(candidate),
  ]);
  const priceValues = visibleCandles.flatMap(candle => [
    candle.high,
    candle.low,
    state.indicators.vwap ? candle.vwap : null,
    state.indicators.ema_20 ? candle.ema_20 : null,
    state.indicators.ema_50 ? candle.ema_50 : null,
    state.indicators.ema_200 ? candle.ema_200 : null,
    profile.poc_price,
    profile.value_area_low,
    profile.value_area_high,
    ...patternLevels,
    ...candidateLevels,
  ]).map(Number).filter(Number.isFinite);
  const minPrice = Math.min(...priceValues);
  const maxPrice = Math.max(...priceValues);
  const [scaledMinPrice, scaledMaxPrice] = applyYScale(minPrice, maxPrice);
  const yPrice = value => scaleLinear(Number(value), scaledMinPrice, scaledMaxPrice, priceBox.bottom, priceBox.top);
  const xAt = index => scaleLinear(index, 0, Math.max(visibleCandles.length - 1, 1), plotBox.left, plotBox.right);
  const volumeMax = Math.max(...visibleCandles.map(candle => Number(candle.volume || 0)), 1);
  const yVolume = value => scaleLinear(Number(value), 0, volumeMax, volumeBox.bottom, volumeBox.top);
  const yRsi = value => scaleLinear(Number(value), 0, 100, rsiBox.bottom, rsiBox.top);
  const candleWidth = Math.max(2, Math.min(9, (plotBox.right - plotBox.left) / visibleCandles.length * 0.58));

  drawBackground(ctx, width, height);
  drawGrid(ctx, priceBox, scaledMinPrice, scaledMaxPrice, yPrice);
  if (state.indicators.profile) drawVolumeProfile(ctx, priceBox, profile, yPrice);
  if (state.indicators.profile) drawVolumeProfileBars(ctx, priceBox, profile, yPrice);
  if (state.indicators.volume) drawVolumeBars(ctx, visibleCandles, xAt, yVolume, volumeBox, candleWidth);
  drawCandles(ctx, visibleCandles, xAt, yPrice, candleWidth);
  if (state.indicators.vwap) drawLine(ctx, visibleCandles, "vwap", xAt, yPrice, "#6b5fbd");
  if (state.indicators.ema_20) drawLine(ctx, visibleCandles, "ema_20", xAt, yPrice, "#0f7c68");
  if (state.indicators.ema_50) drawLine(ctx, visibleCandles, "ema_50", xAt, yPrice, "#b76b00");
  if (state.indicators.ema_200) drawLine(ctx, visibleCandles, "ema_200", xAt, yPrice, "#435469");
  if (state.indicators.patterns) drawPatternCandidates(ctx, visibleCandidates, visibleCandles, xAt, yPrice, plotBox);
  if (state.indicators.patterns) drawPatterns(ctx, visiblePatterns, visibleCandles, xAt, yPrice, plotBox);
  if (state.indicators.rsi) drawRsiPanel(ctx, visibleCandles, xAt, yRsi, rsiBox);
  drawAxisLabels(ctx, chart.symbol, visibleCandles, priceBox, volumeBox, rsiBox, scaledMinPrice, scaledMaxPrice);

  legend.innerHTML = activeLegendItems().map(item => `<span>${item}</span>`).join("");
  renderSignalEvidence(chart, visiblePatterns, visibleCandidates);
}

function visibleChartCandles(candles) {
  const total = candles.length;
  if (!total) return [];
  state.chartWindow = clamp(Math.round(state.chartWindow || 120), 30, total);
  state.chartOffset = clamp(Math.round(state.chartOffset || 0), 0, Math.max(total - state.chartWindow, 0));
  const end = total - state.chartOffset;
  const start = Math.max(0, end - state.chartWindow);
  return candles.slice(start, end);
}

function clamp(value, min, max) {
  return Math.max(min, Math.min(max, value));
}

function applyYScale(minPrice, maxPrice) {
  const rawRange = maxPrice - minPrice || 1;
  const center = (minPrice + maxPrice) / 2;
  const scale = clamp(Number(state.chartYScale || 1), CHART_Y_SCALE_MIN, CHART_Y_SCALE_MAX);
  const range = rawRange * scale;
  const pad = range * 0.08 || 1;
  return [center - range / 2 - pad, center + range / 2 + pad];
}

function setChartYScale(value, options = {}) {
  state.chartYScale = clamp(Number(value) || 1, CHART_Y_SCALE_MIN, CHART_Y_SCALE_MAX);
  updateChartYScaleControl();
  if (options.render !== false) {
    renderCandlestickChart(state.chart);
  }
}

function adjustChartYScale(multiplier) {
  setChartYScale((Number(state.chartYScale) || 1) * multiplier);
}

function updateChartYScaleControl() {
  const input = document.querySelector("#chart-y-zoom");
  const output = document.querySelector("#chart-y-scale-value");
  const scale = clamp(Number(state.chartYScale || 1), CHART_Y_SCALE_MIN, CHART_Y_SCALE_MAX);
  state.chartYScale = scale;
  if (input) input.value = scale.toFixed(2);
  if (output) output.textContent = `${Math.round(scale * 100)}%`;
}

function patternEventKey(pattern) {
  return [
    pattern.pattern_name,
    pattern.detection_date,
    pattern.neckline_price || "",
    pattern.support_price || "",
    pattern.resistance_price || "",
    pattern.target_price || "",
  ].join("|");
}

function selectedVisiblePatterns(patterns, visibleDates) {
  if (!state.indicators.patterns) {
    return [];
  }
  const enabled = state.enabledPatternTypes.size
    ? state.enabledPatternTypes
    : new Set(patterns.map(pattern => pattern.pattern_name));
  const filtered = patterns.filter(
    pattern => visibleDates.has(pattern.detection_date) && enabled.has(pattern.pattern_name)
  );
  if (state.selectedPatternEventKey) {
    return filtered.filter(pattern => patternEventKey(pattern) === state.selectedPatternEventKey);
  }
  return filtered.slice(0, 5);
}

function selectedVisibleCandidates(candidates, visibleDates) {
  if (!state.indicators.patterns || state.selectedPatternEventKey) {
    return [];
  }
  const enabled = state.enabledPatternTypes.size
    ? state.enabledPatternTypes
    : new Set(candidates.map(candidate => candidate.pattern_name));
  return candidates
    .filter(candidate => visibleDates.has(candidate.trade_date) && enabled.has(candidate.pattern_name))
    .slice(0, 4);
}

function renderPatternControls(patterns, candidates, visiblePatterns) {
  const filterWrap = document.querySelector("#pattern-type-filters");
  const eventSelect = document.querySelector("#pattern-event-select");
  if (!filterWrap || !eventSelect) return;
  const patternNames = [
    ...new Set([
      ...patterns.map(pattern => pattern.pattern_name),
      ...candidates.map(candidate => candidate.pattern_name),
    ]),
  ].sort();
  if (!state.enabledPatternTypes.size && patternNames.length) {
    state.enabledPatternTypes = new Set(patternNames);
  }
  filterWrap.innerHTML = patternNames
    .map(
      name => `<label><input type="checkbox" data-pattern-type="${name}" ${
        state.enabledPatternTypes.has(name) ? "checked" : ""
      } /> ${name}</label>`
    )
    .join("");
  filterWrap.querySelectorAll("[data-pattern-type]").forEach(input => {
    input.addEventListener("change", () => {
      if (input.checked) {
        state.enabledPatternTypes.add(input.dataset.patternType);
      } else {
        state.enabledPatternTypes.delete(input.dataset.patternType);
      }
      state.selectedPatternEventKey = "";
      renderCandlestickChart(state.chart);
    });
  });

  const enabled = state.enabledPatternTypes.size ? state.enabledPatternTypes : new Set(patternNames);
  const selectable = patterns.filter(pattern => enabled.has(pattern.pattern_name));
  const keys = new Set(selectable.map(patternEventKey));
  if (state.selectedPatternEventKey && !keys.has(state.selectedPatternEventKey)) {
    state.selectedPatternEventKey = "";
  }
  eventSelect.innerHTML =
    `<option value="">Visible recent patterns</option>` +
    selectable
      .map(pattern => {
        const key = patternEventKey(pattern);
        const selected = key === state.selectedPatternEventKey ? "selected" : "";
        return `<option value="${key}" ${selected}>${pattern.detection_date} · ${pattern.pattern_name} · ${Number(
          pattern.confluence_score || 0
        ).toFixed(2)}</option>`;
      })
      .join("");
  eventSelect.disabled = !selectable.length || !state.indicators.patterns;
}

function activeLegendItems() {
  const items = ["Candles"];
  if (state.indicators.vwap) items.push("VWAP");
  if (state.indicators.ema_20) items.push("EMA20");
  if (state.indicators.ema_50) items.push("EMA50");
  if (state.indicators.ema_200) items.push("EMA200");
  if (state.indicators.volume) items.push("Volume");
  if (state.indicators.rsi) items.push("RSI14");
  if (state.indicators.profile) items.push("POC / Value Area");
  if (state.indicators.patterns) items.push("Pattern markers / forming setups");
  return items;
}

function prepareCanvas(canvas) {
  const dpr = window.devicePixelRatio || 1;
  const rect = canvas.getBoundingClientRect();
  canvas.width = Math.round(rect.width * dpr);
  canvas.height = Math.round(rect.height * dpr);
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  return ctx;
}

function clearChart(canvas) {
  const ctx = prepareCanvas(canvas);
  ctx.clearRect(0, 0, canvas.clientWidth, canvas.clientHeight);
}

function scaleLinear(value, domainMin, domainMax, rangeMin, rangeMax) {
  if (domainMax === domainMin) return (rangeMin + rangeMax) / 2;
  return rangeMin + ((value - domainMin) / (domainMax - domainMin)) * (rangeMax - rangeMin);
}

function drawBackground(ctx, width, height) {
  ctx.clearRect(0, 0, width, height);
  ctx.fillStyle = "#ffffff";
  ctx.fillRect(0, 0, width, height);
}

function drawGrid(ctx, box, minPrice, maxPrice, yPrice) {
  ctx.strokeStyle = "#e3e8eb";
  ctx.fillStyle = "#667681";
  ctx.font = "12px Inter, sans-serif";
  ctx.lineWidth = 1;
  for (let i = 0; i <= 4; i += 1) {
    const price = minPrice + ((maxPrice - minPrice) / 4) * i;
    const y = yPrice(price);
    ctx.beginPath();
    ctx.moveTo(box.left, y);
    ctx.lineTo(box.right, y);
    ctx.stroke();
    ctx.fillText(formatDecimal(price), 6, y + 4);
  }
}

function drawVolumeProfile(ctx, box, profile, yPrice) {
  const low = Number(profile.value_area_low);
  const high = Number(profile.value_area_high);
  const poc = Number(profile.poc_price);
  if (Number.isFinite(low) && Number.isFinite(high)) {
    ctx.fillStyle = "rgba(56, 183, 143, 0.10)";
    ctx.fillRect(box.left, yPrice(high), box.right - box.left, Math.max(1, yPrice(low) - yPrice(high)));
  }
  if (Number.isFinite(poc)) {
    drawHorizontalLevel(ctx, box, yPrice(poc), "#0b4f42", "POC");
  }
}

function drawVolumeProfileBars(ctx, box, profile, yPrice) {
  const bins = (profile.bins || [])
    .map(bin => ({ price: Number(bin.price), volume: Number(bin.volume) }))
    .filter(bin => Number.isFinite(bin.price) && Number.isFinite(bin.volume));
  if (!bins.length) return;
  const maxVolume = Math.max(...bins.map(bin => bin.volume), 1);
  const bandLeft = box.right - 126;
  const bandRight = box.right - 8;
  const barMaxWidth = bandRight - bandLeft;
  const sorted = bins.sort((a, b) => a.price - b.price);
  sorted.forEach((bin, index) => {
    const next = sorted[index + 1];
    const previous = sorted[index - 1];
    const halfStep = next
      ? Math.abs(next.price - bin.price) / 2
      : previous
        ? Math.abs(bin.price - previous.price) / 2
        : 1;
    const top = yPrice(bin.price + halfStep);
    const bottom = yPrice(bin.price - halfStep);
    const height = Math.max(2, bottom - top);
    const width = Math.max(2, (bin.volume / maxVolume) * barMaxWidth);
    const isValueArea =
      Number.isFinite(Number(profile.value_area_low)) &&
      Number.isFinite(Number(profile.value_area_high)) &&
      bin.price >= Number(profile.value_area_low) &&
      bin.price <= Number(profile.value_area_high);
    ctx.fillStyle = isValueArea ? "rgba(64, 113, 224, 0.70)" : "rgba(222, 176, 66, 0.68)";
    ctx.fillRect(bandRight - width, top, width, height);
  });
  ctx.strokeStyle = "#667681";
  ctx.strokeRect(bandLeft, box.top, bandRight - bandLeft, box.bottom - box.top);
}

function drawVolumeBars(ctx, candles, xAt, yVolume, box, candleWidth) {
  candles.forEach((candle, index) => {
    const up = Number(candle.close) >= Number(candle.open);
    ctx.fillStyle = up ? "rgba(13, 107, 63, 0.32)" : "rgba(155, 74, 22, 0.30)";
    const x = xAt(index) - candleWidth / 2;
    const y = yVolume(candle.volume);
    ctx.fillRect(x, y, candleWidth, box.bottom - y);
  });
}

function drawCandles(ctx, candles, xAt, yPrice, candleWidth) {
  candles.forEach((candle, index) => {
    const open = Number(candle.open);
    const close = Number(candle.close);
    const high = Number(candle.high);
    const low = Number(candle.low);
    const up = close >= open;
    const x = xAt(index);
    ctx.strokeStyle = up ? "#0d6b3f" : "#9b4a16";
    ctx.fillStyle = up ? "#0d6b3f" : "#9b4a16";
    ctx.beginPath();
    ctx.moveTo(x, yPrice(high));
    ctx.lineTo(x, yPrice(low));
    ctx.stroke();
    const bodyTop = yPrice(Math.max(open, close));
    const bodyHeight = Math.max(1, Math.abs(yPrice(open) - yPrice(close)));
    ctx.fillRect(x - candleWidth / 2, bodyTop, candleWidth, bodyHeight);
  });
}

function drawLine(ctx, candles, field, xAt, yPrice, color) {
  ctx.strokeStyle = color;
  ctx.lineWidth = 1.6;
  ctx.beginPath();
  let hasPoint = false;
  candles.forEach((candle, index) => {
    const value = Number(candle[field]);
    if (!Number.isFinite(value)) return;
    const x = xAt(index);
    const y = yPrice(value);
    if (!hasPoint) {
      ctx.moveTo(x, y);
      hasPoint = true;
    } else {
      ctx.lineTo(x, y);
    }
  });
  if (hasPoint) ctx.stroke();
}

function drawPatterns(ctx, patterns, candles, xAt, yPrice, box) {
  const dateIndex = new Map(candles.map((candle, index) => [candle.trade_date, index]));
  patterns.forEach((pattern, markerIndex) => {
    const index = dateIndex.get(pattern.detection_date);
    if (index === undefined) return;
    const x = xAt(index);
    drawPatternGeometry(ctx, pattern, candles, xAt, yPrice, box);
    ctx.strokeStyle = "rgba(18, 107, 91, 0.55)";
    ctx.setLineDash([4, 4]);
    ctx.beginPath();
    ctx.moveTo(x, box.top);
    ctx.lineTo(x, box.bottom);
    ctx.stroke();
    ctx.setLineDash([]);
    ctx.fillStyle = "#126b5b";
    ctx.beginPath();
    ctx.arc(x, box.top + 12, 9, 0, Math.PI * 2);
    ctx.fill();
    ctx.fillStyle = "#ffffff";
    ctx.font = "11px Inter, sans-serif";
    ctx.textAlign = "center";
    ctx.fillText(String(markerIndex + 1), x, box.top + 16);
    ctx.textAlign = "left";
    [
      [pattern.neckline_price, "#126b5b", "neckline"],
      [pattern.support_price, "#9b4a16", "support"],
      [pattern.resistance_price, "#435469", "resistance"],
      [pattern.target_price, "#6b5fbd", "target"],
    ].forEach(([value, color]) => {
      if (Number.isFinite(Number(value))) {
        drawHorizontalLevel(ctx, box, yPrice(value), color);
      }
    });
  });
}

function drawPatternCandidates(ctx, candidates, candles, xAt, yPrice, box) {
  const dateIndex = new Map(candles.map((candle, index) => [candle.trade_date, index]));
  candidates.forEach(candidate => {
    const index = dateIndex.get(candidate.trade_date);
    if (index === undefined) return;
    drawPatternGeometry(ctx, candidate, candles, xAt, yPrice, box, { candidate: true });
    const x = xAt(index);
    const y = yPrice(candidate.trigger_price || candles[index].close);
    ctx.save();
    ctx.fillStyle = "rgba(255, 255, 255, 0.92)";
    ctx.strokeStyle = "rgba(67, 84, 105, 0.45)";
    ctx.lineWidth = 1;
    const label = `${candidate.pattern_name} ${(Number(candidate.completion_score || 0) * 100).toFixed(0)}%`;
    const width = ctx.measureText(label).width + 12;
    const left = Math.max(box.left + 2, Math.min(x - width / 2, box.right - width - 2));
    const top = Math.max(box.top + 4, y - 28);
    ctx.fillRect(left, top, width, 18);
    ctx.strokeRect(left, top, width, 18);
    ctx.fillStyle = "#435469";
    ctx.font = "11px Inter, sans-serif";
    ctx.fillText(label, left + 6, top + 13);
    ctx.restore();
  });
}

function drawPatternGeometry(ctx, pattern, candles, xAt, yPrice, box, options = {}) {
  const geometry = pattern.features?.geometry;
  if (!geometry) return;
  const dateIndex = new Map(candles.map((candle, index) => [candle.trade_date, index]));
  const points = (geometry.points || [])
    .map(point => {
      const index = dateIndex.get(point.date);
      const price = Number(point.price);
      if (index === undefined || !Number.isFinite(price)) return null;
      return {
        x: xAt(index),
        y: yPrice(price),
        role: point.role,
      };
    })
    .filter(Boolean);

  drawWedgeFill(ctx, points, pattern, options);

  if (points.length >= 2) {
    ctx.save();
    ctx.strokeStyle = patternGeometryColor(pattern.pattern_name);
    ctx.globalAlpha = options.candidate ? 0.72 : 1;
    ctx.lineWidth = options.candidate ? 2 : 3;
    if (options.candidate) ctx.setLineDash([6, 5]);
    drawGeometrySegments(ctx, points, geometry.segments || []);
    ctx.restore();
  }

  drawGeometryLevels(ctx, box, geometry.levels || {}, yPrice);

  points.forEach(point => {
    ctx.save();
    ctx.fillStyle = "#ffffff";
    ctx.strokeStyle = patternGeometryColor(pattern.pattern_name);
    ctx.globalAlpha = options.candidate ? 0.82 : 1;
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.arc(point.x, point.y, 5, 0, Math.PI * 2);
    ctx.fill();
    ctx.stroke();
    drawPointLabel(ctx, roleLabel(point.role), point.x, point.y, box);
    ctx.restore();
  });
}

function drawGeometrySegments(ctx, points, segments) {
  const byRole = new Map(points.map(point => [point.role, point]));
  if (segments.length) {
    segments.forEach(([fromRole, toRole]) => {
      const from = byRole.get(fromRole);
      const to = byRole.get(toRole);
      if (!from || !to) return;
      ctx.beginPath();
      ctx.moveTo(from.x, from.y);
      ctx.lineTo(to.x, to.y);
      ctx.stroke();
    });
    return;
  }
  ctx.beginPath();
  points.forEach((point, index) => {
    if (index === 0) {
      ctx.moveTo(point.x, point.y);
    } else {
      ctx.lineTo(point.x, point.y);
    }
  });
  ctx.stroke();
}

function drawWedgeFill(ctx, points, pattern, options) {
  const byRole = new Map(points.map(point => [point.role, point]));
  const lowerStart = byRole.get("lower_start");
  const upperStart = byRole.get("upper_start");
  const lowerEnd = byRole.get("lower_end");
  const upperEnd = byRole.get("upper_end");
  if (!lowerStart || !upperStart || !lowerEnd || !upperEnd) return;
  ctx.save();
  ctx.fillStyle = pattern.direction === "bearish" ? "rgba(155, 74, 22, 0.10)" : "rgba(18, 107, 91, 0.10)";
  ctx.globalAlpha = options.candidate ? 0.55 : 1;
  ctx.beginPath();
  ctx.moveTo(upperStart.x, upperStart.y);
  ctx.lineTo(upperEnd.x, upperEnd.y);
  ctx.lineTo(lowerEnd.x, lowerEnd.y);
  ctx.lineTo(lowerStart.x, lowerStart.y);
  ctx.closePath();
  ctx.fill();
  ctx.restore();
}

function geometryPrices(pattern) {
  const geometry = pattern.features?.geometry;
  if (!geometry) return [];
  const pointPrices = (geometry.points || []).map(point => point.price);
  const levelPrices = Object.values(geometry.levels || {});
  return [...pointPrices, ...levelPrices];
}

function drawGeometryLevels(ctx, box, levels, yPrice) {
  [
    ["neckline", levels.neckline, "#126b5b"],
    ["support", levels.support, "#9b4a16"],
    ["resistance", levels.resistance, "#435469"],
    ["target", levels.target, "#6b5fbd"],
  ].forEach(([label, value, color]) => {
    const price = Number(value);
    if (Number.isFinite(price)) {
      drawHorizontalLevel(ctx, box, yPrice(price), color, label);
    }
  });
}

function drawPointLabel(ctx, label, x, y, box) {
  ctx.font = "10px Inter, sans-serif";
  const paddingX = 4;
  const textWidth = ctx.measureText(label).width;
  const labelWidth = textWidth + paddingX * 2;
  const labelHeight = 16;
  const labelX = Math.max(box.left + 2, Math.min(x - labelWidth / 2, box.right - labelWidth - 2));
  const labelY = Math.max(box.top + 2, y - 24);
  ctx.fillStyle = "rgba(255, 255, 255, 0.90)";
  ctx.fillRect(labelX, labelY, labelWidth, labelHeight);
  ctx.strokeStyle = "rgba(102, 118, 129, 0.35)";
  ctx.strokeRect(labelX, labelY, labelWidth, labelHeight);
  ctx.fillStyle = "#1a2228";
  ctx.fillText(label, labelX + paddingX, labelY + 11);
}

function roleLabel(role) {
  return {
    left_shoulder: "LS",
    head: "H",
    right_shoulder: "RS",
    first_bottom: "B1",
    second_bottom: "B2",
    support_start: "S1",
    support_end: "S2",
    upper_start: "U1",
    upper_end: "U2",
    lower_start: "L1",
    lower_end: "L2",
    breakout: "BO",
    breakdown: "BD",
    forming: "WAIT",
  }[role] || role;
}

function patternGeometryColor(patternName) {
  return {
    double_bottom: "#126b5b",
    inverse_head_and_shoulders: "#0f7c68",
    ascending_triangle: "#435469",
    descending_triangle: "#9b4a16",
    symmetrical_triangle: "#435469",
    high_tight_flag: "#6b5fbd",
    rising_wedge: "#9b4a16",
    falling_wedge: "#126b5b",
  }[patternName] || "#126b5b";
}

function drawHorizontalLevel(ctx, box, y, color, label = "") {
  ctx.strokeStyle = color;
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(box.left, y);
  ctx.lineTo(box.right, y);
  ctx.stroke();
  if (label) {
    ctx.fillStyle = color;
    ctx.font = "11px Inter, sans-serif";
    ctx.fillText(label, box.right - 76, y - 4);
  }
}

function drawRsiPanel(ctx, candles, xAt, yRsi, box) {
  ctx.strokeStyle = "#e3e8eb";
  ctx.strokeRect(box.left, box.top, box.right - box.left, box.bottom - box.top);
  [30, 70].forEach(level => {
    ctx.strokeStyle = "#d9e0e4";
    ctx.beginPath();
    ctx.moveTo(box.left, yRsi(level));
    ctx.lineTo(box.right, yRsi(level));
    ctx.stroke();
  });
  drawLine(ctx, candles, "rsi_14", xAt, yRsi, "#6b5fbd");
  ctx.fillStyle = "#667681";
  ctx.font = "12px Inter, sans-serif";
  ctx.fillText("RSI14", 8, box.top + 14);
  candles.forEach((candle, index) => {
    if (candle.rsi_divergence && candle.rsi_divergence !== "none") {
      ctx.fillStyle = candle.rsi_divergence === "bullish" ? "#0d6b3f" : "#9b4a16";
      ctx.fillRect(xAt(index) - 2, box.top, 4, box.bottom - box.top);
    }
  });
}

function drawAxisLabels(ctx, symbol, candles, priceBox, volumeBox, rsiBox, minPrice, maxPrice) {
  ctx.fillStyle = "#1a2228";
  ctx.font = "13px Inter, sans-serif";
  ctx.fillText(`${symbol} daily`, priceBox.left, 15);
  ctx.fillStyle = "#667681";
  ctx.fillText("Volume", 8, volumeBox.top + 14);
  ctx.fillText("0", 26, rsiBox.bottom);
  ctx.fillText("100", 12, rsiBox.top + 4);
  const first = candles[0]?.trade_date || "";
  const last = candles[candles.length - 1]?.trade_date || "";
  ctx.fillText(first, priceBox.left, rsiBox.bottom + 18);
  ctx.fillText(last, Math.max(priceBox.left, priceBox.right - 86), rsiBox.bottom + 18);
  ctx.fillText(`${formatDecimal(minPrice)} - ${formatDecimal(maxPrice)}`, priceBox.right - 150, 15);
}

function renderSignalEvidence(chart, visiblePatterns, visibleCandidates = []) {
  const container = document.querySelector("#signal-evidence");
  if (!container) return;
  const latest = chart?.candles?.[chart.candles.length - 1] || {};
  const sentimentItems = chart?.sentiment_items || [];
  const sentimentScore = average(sentimentItems.map(item => Number(item.sentiment_score)).filter(Number.isFinite));
  const sentimentLabel = sentimentScore > 0.15 ? "bullish" : sentimentScore < -0.15 ? "bearish" : "neutral";
  const technicalReasons = buildTechnicalReasons(latest);
  const patternHtml = visiblePatterns.length
    ? visiblePatterns
        .map(
          (pattern, index) =>
            `<li><b>${index + 1}. ${pattern.pattern_name}</b> ${pattern.detection_date} · ${pattern.features?.direction || "bullish"} · score ${Number(pattern.confluence_score || 0).toFixed(2)} · ${pattern.features?.pattern_context || "context unknown"} · prior ${pattern.features?.prior_trend || "unknown"} · volume x${pattern.features?.breakout_volume_ratio || "-"} · target ${formatDecimal(pattern.target_price)}</li>`
        )
        .join("")
    : "<li>No visible pattern overlays in current zoom window</li>";
  const candidateHtml = visibleCandidates.length
    ? visibleCandidates
        .map(
          candidate =>
            `<li><b>${candidate.pattern_name}</b> forming ${(Number(candidate.completion_score || 0) * 100).toFixed(
              0
            )}% · trigger ${formatDecimal(candidate.trigger_price)} · ${candidate.features?.missing_condition || ""}</li>`
        )
        .join("")
    : "<li>No forming pattern candidates in current zoom window</li>";
  const linkHtml = sentimentItems.length
    ? sentimentItems
        .slice(0, 5)
        .map(item => `<li><a href="${item.url}" target="_blank" rel="noreferrer">${item.title}</a><span>${item.source || ""} · ${item.sentiment_label} · ${item.sentiment_reason}</span></li>`)
        .join("")
    : "<li>No sentiment links stored for this symbol</li>";
  container.innerHTML = `
    <div class="evidence-card">
      <span>Signal Bias</span>
      <strong class="${sentimentLabel}">${sentimentLabel}</strong>
      <p>Sentiment confidence ${Number(Math.abs(sentimentScore || 0)).toFixed(2)}</p>
    </div>
    <div class="evidence-card">
      <span>Technical Reasons</span>
      <ul>${technicalReasons.map(reason => `<li>${reason}</li>`).join("")}</ul>
    </div>
    <div class="evidence-card">
      <span>Pattern Evidence</span>
      <ul>${patternHtml}${candidateHtml}</ul>
    </div>
    <div class="evidence-card links">
      <span>Sentiment Links</span>
      <ul>${linkHtml}</ul>
    </div>
  `;
}

function buildTechnicalReasons(candle) {
  const reasons = [];
  const close = Number(candle.close);
  if (Number.isFinite(close) && Number.isFinite(Number(candle.vwap))) {
    reasons.push(close >= Number(candle.vwap) ? "Close is above or equal to VWAP" : "Close is below VWAP");
  }
  if (Number.isFinite(Number(candle.ema_20)) && Number.isFinite(Number(candle.ema_50))) {
    reasons.push(Number(candle.ema_20) >= Number(candle.ema_50) ? "EMA20 is above EMA50" : "EMA20 is below EMA50");
  }
  if (candle.rsi_divergence && candle.rsi_divergence !== "none") {
    reasons.push(`RSI divergence is ${candle.rsi_divergence}`);
  }
  if (Number.isFinite(Number(candle.rsi_14))) {
    reasons.push(`RSI14 is ${Number(candle.rsi_14).toFixed(1)}`);
  }
  return reasons.length ? reasons : ["No technical evidence available"];
}

function average(values) {
  if (!values.length) return 0;
  return values.reduce((total, value) => total + value, 0) / values.length;
}

async function postJson(url, payload) {
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new Error(error.detail || `Request failed: ${response.status}`);
  }
  return response.json();
}

async function createPaperAccount(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const payload = formPayloadWithLists(form, []);
  await postJson("/api/paper/accounts", payload);
  await loadPaperDashboard();
}

async function createPaperStrategy(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const payload = formPayloadWithLists(form, ["pattern_names", "selected_indicators"]);
  await postJson("/api/paper/strategies", payload);
  await loadPaperDashboard();
}

async function runPaperBacktest(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const payload = formPayloadWithLists(form, []);
  await postJson("/api/paper/backtests", payload);
  await loadPaperDashboard();
}

function formPayloadWithLists(form, listKeys) {
  const formData = new FormData(form);
  const payload = Object.fromEntries(formData.entries());
  form.querySelectorAll("[data-money-input]").forEach(input => {
    if (input.name) payload[input.name] = String(input.value || "").replace(/[^0-9.-]/g, "");
  });
  listKeys.forEach(key => {
    payload[key] = formData.getAll(key);
  });
  return payload;
}

function formatMoneyInput(input) {
  const digits = String(input.value || "").replace(/\D/g, "");
  input.value = digits ? new Intl.NumberFormat("ko-KR").format(Number(digits)) : "";
  const readable = document.querySelector(`#${input.getAttribute("aria-describedby")}`);
  if (readable) readable.textContent = koreanMoneyLabel(Number(digits || 0));
}

function koreanMoneyLabel(value) {
  if (!Number.isFinite(value) || value <= 0) return "금액을 입력하세요";
  if (value >= 100000000) {
    const amount = value / 100000000;
    return `${new Intl.NumberFormat("ko-KR", { maximumFractionDigits: 2 }).format(amount)}억원`;
  }
  if (value >= 10000) {
    return `${new Intl.NumberFormat("ko-KR", { maximumFractionDigits: 1 }).format(value / 10000)}만원`;
  }
  return `${new Intl.NumberFormat("ko-KR").format(value)}원`;
}

function initializeChartInteractions() {
  const canvas = document.querySelector("#symbol-chart");
  if (!canvas) return;
  canvas.addEventListener("wheel", handleChartWheel, { passive: false });
  canvas.addEventListener("pointerdown", handleChartPointerDown);
  canvas.addEventListener("pointermove", handleChartPointerMove);
  canvas.addEventListener("pointerup", handleChartPointerUp);
  canvas.addEventListener("pointercancel", handleChartPointerUp);
  canvas.addEventListener("dblclick", resetChartView);
}

function chartInteractionBounds(canvas) {
  const rect = canvas.getBoundingClientRect();
  const height = canvas.clientHeight;
  const width = canvas.clientWidth;
  const priceBox = { left: 56, top: 22, right: width - 18, bottom: Math.round(height * 0.64) };
  return { rect, priceBox };
}

function isChartYAxisHit(event) {
  const { rect, priceBox } = chartInteractionBounds(event.currentTarget);
  const x = event.clientX - rect.left;
  const y = event.clientY - rect.top;
  if (y < priceBox.top || y > priceBox.bottom) return false;
  return x <= priceBox.left || x >= priceBox.right - 32;
}

function handleChartWheel(event) {
  const candles = state.chart?.candles || [];
  if (!candles.length) return;
  event.preventDefault();
  if (event.shiftKey || isChartYAxisHit(event)) {
    adjustChartYScale(event.deltaY > 0 ? 1.08 : 0.92);
    return;
  }
  const rect = event.currentTarget.getBoundingClientRect();
  const ratio = clamp((event.clientX - rect.left) / Math.max(rect.width, 1), 0, 1);
  const total = candles.length;
  const currentWindow = clamp(Math.round(state.chartWindow || 120), 30, total);
  const currentOffset = clamp(Math.round(state.chartOffset || 0), 0, Math.max(total - currentWindow, 0));
  const currentEnd = total - currentOffset;
  const currentStart = Math.max(0, currentEnd - currentWindow);
  const anchor = currentStart + ratio * currentWindow;
  const zoomFactor = event.deltaY > 0 ? 1.18 : 0.84;
  const nextWindow = clamp(Math.round(currentWindow * zoomFactor), 30, total);
  const nextStart = clamp(Math.round(anchor - ratio * nextWindow), 0, Math.max(total - nextWindow, 0));
  state.chartWindow = nextWindow;
  state.chartOffset = clamp(total - (nextStart + nextWindow), 0, Math.max(total - nextWindow, 0));
  renderCandlestickChart(state.chart);
}

function handleChartPointerDown(event) {
  const candles = state.chart?.candles || [];
  if (!candles.length) return;
  event.currentTarget.setPointerCapture?.(event.pointerId);
  if (isChartYAxisHit(event)) {
    state.chartYDrag = {
      pointerId: event.pointerId,
      startY: event.clientY,
      startScale: state.chartYScale,
    };
    event.currentTarget.style.cursor = "ns-resize";
    return;
  }
  state.chartDrag = {
    pointerId: event.pointerId,
    startX: event.clientX,
    dragStartOffset: state.chartOffset,
  };
}

function handleChartPointerMove(event) {
  const yDrag = state.chartYDrag;
  if (yDrag?.pointerId === event.pointerId) {
    const deltaY = event.clientY - yDrag.startY;
    const multiplier = Math.exp(deltaY / 220);
    setChartYScale(yDrag.startScale * multiplier);
    return;
  }
  const drag = state.chartDrag;
  const candles = state.chart?.candles || [];
  if (!drag || drag.pointerId !== event.pointerId || !candles.length) {
    event.currentTarget.style.cursor = isChartYAxisHit(event) ? "ns-resize" : "grab";
    return;
  }
  const canvas = event.currentTarget;
  canvas.style.cursor = "grabbing";
  const candlesPerPixel = state.chartWindow / Math.max(canvas.clientWidth, 1);
  const deltaCandles = Math.round((event.clientX - drag.startX) * candlesPerPixel);
  state.chartOffset = clamp(
    drag.dragStartOffset + deltaCandles,
    0,
    Math.max(candles.length - state.chartWindow, 0)
  );
  renderCandlestickChart(state.chart);
}

function handleChartPointerUp(event) {
  if (state.chartDrag?.pointerId === event.pointerId) {
    state.chartDrag = null;
  }
  if (state.chartYDrag?.pointerId === event.pointerId) {
    state.chartYDrag = null;
  }
  event.currentTarget.style.cursor = isChartYAxisHit(event) ? "ns-resize" : "grab";
}

function resetChartView() {
  state.chartOffset = 0;
  renderCandlestickChart(state.chart);
}

function initializeSortableTables() {
  document.querySelectorAll("table").forEach((table, tableIndex) => {
    table.querySelectorAll("th").forEach((header, columnIndex) => {
      header.classList.add("sortable");
      header.addEventListener("click", () => sortTable(table, tableIndex, columnIndex));
    });
  });
}

function sortTable(table, tableIndex, columnIndex) {
  const tbody = table.querySelector("tbody");
  if (!tbody) return;
  const key = `${tableIndex}:${columnIndex}`;
  const direction = state.sort[key] === "asc" ? "desc" : "asc";
  state.sort[key] = direction;
  const rows = Array.from(tbody.querySelectorAll("tr"));
  rows.sort((a, b) => compareCell(a.children[columnIndex]?.textContent, b.children[columnIndex]?.textContent, direction));
  tbody.replaceChildren(...rows);
}

function compareCell(left, right, direction) {
  const leftValue = sortableValue(left || "");
  const rightValue = sortableValue(right || "");
  const result = typeof leftValue === "number" && typeof rightValue === "number"
    ? leftValue - rightValue
    : String(leftValue).localeCompare(String(rightValue), "ko");
  return direction === "asc" ? result : -result;
}

function sortableValue(value) {
  const normalized = value.trim().replace(/,/g, "").replace("%", "").split("/")[0].trim();
  const numeric = Number(normalized.replace(/[^\d.-]/g, ""));
  return normalized && Number.isFinite(numeric) ? numeric : value.trim();
}

document.querySelector("#refresh").addEventListener("click", loadDashboard);
document.querySelector("#pattern-filter").addEventListener("change", render);
document.querySelector("#symbol-filter").addEventListener("input", render);
document.querySelector("#retest-filter").addEventListener("change", render);
document.querySelector("#chart-symbol").addEventListener("change", event => loadSymbolChart(event.target.value));
document.querySelector("#chart-window").addEventListener("change", event => {
  state.chartWindow = Number(event.target.value);
  state.chartOffset = 0;
  renderCandlestickChart(state.chart);
});
document.querySelector("#chart-y-zoom").addEventListener("input", event => {
  setChartYScale(event.target.value);
});
document.querySelector("#chart-y-reset").addEventListener("click", () => {
  setChartYScale(1);
});
document.querySelector("#pattern-event-select").addEventListener("change", event => {
  state.selectedPatternEventKey = event.target.value;
  renderCandlestickChart(state.chart);
});
document.querySelectorAll("[data-indicator]").forEach(input => {
  input.addEventListener("change", () => {
    state.indicators[input.dataset.indicator] = input.checked;
    renderCandlestickChart(state.chart);
  });
});
document.querySelectorAll("[data-tab]").forEach(button => {
  button.addEventListener("click", () => setActiveTab(button.dataset.tab));
});
document.querySelectorAll("[data-pulse-filter]").forEach(button => {
  button.addEventListener("click", () => {
    state.pulseFilter = button.dataset.pulseFilter;
    document.querySelectorAll("[data-pulse-filter]").forEach(item => item.classList.toggle("active", item === button));
    renderDriverGrid(state.pulse || { drivers: [], indicators: [] });
  });
});
document.querySelectorAll("#pattern-horizon-filter, #pattern-sample-filter, #pattern-verdict-filter").forEach(input => {
  input.addEventListener("change", () => renderPerformance(state.dashboard?.performance || []));
});
document.querySelectorAll("#universe-filter, #universe-sort").forEach(input => {
  input.addEventListener("change", () => renderUniverse(
    state.dashboard?.universe || [],
    state.dashboard?.technicals || [],
    state.dashboard?.sentiment || [],
    state.dashboard?.signals || []
  ));
});
document.querySelector("#universe-search")?.addEventListener("input", () => renderUniverse(
  state.dashboard?.universe || [],
  state.dashboard?.technicals || [],
  state.dashboard?.sentiment || [],
  state.dashboard?.signals || []
));
document.querySelectorAll("[data-strategy-preset]").forEach(button => {
  button.addEventListener("click", () => applyStrategyPreset(button.dataset.strategyPreset));
});
document.querySelectorAll("[data-money-input]").forEach(input => {
  formatMoneyInput(input);
  input.addEventListener("input", () => formatMoneyInput(input));
});
document.querySelectorAll("[data-indicator-close]").forEach(button => button.addEventListener("click", closeIndicatorChart));
document.querySelectorAll("[data-indicator-range]").forEach(button => {
  button.addEventListener("click", () => {
    state.indicatorRange = Number(button.dataset.indicatorRange);
    state.indicatorOffset = 0;
    state.indicatorHoverIndex = null;
    renderIndicatorModal();
  });
});
document.addEventListener("keydown", event => {
  if (event.key === "Escape" && !document.querySelector("#indicator-modal").hidden) closeIndicatorChart();
});
document.querySelector("#paper-strategy-form")?.addEventListener("input", renderStrategyRuleSummary);
document.querySelector("#paper-strategy-form")?.addEventListener("change", renderStrategyRuleSummary);
document.querySelector("#paper-refresh")?.addEventListener("click", loadPaperDashboard);
document.querySelector("#paper-account-form")?.addEventListener("submit", createPaperAccount);
document.querySelector("#paper-strategy-form")?.addEventListener("submit", createPaperStrategy);
document.querySelector("#paper-backtest-form")?.addEventListener("submit", runPaperBacktest);

initializeSortableTables();
initializeChartInteractions();
loadDashboard();

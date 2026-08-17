const state = {
  dashboard: null,
  paper: null,
  activeTab: "overview",
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
    vwap: true,
    ema_20: true,
    ema_50: true,
    ema_200: false,
    volume: true,
    rsi: true,
    profile: true,
    patterns: true,
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
  const response = await fetch("/api/dashboard");
  state.dashboard = await response.json();
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
  document.querySelector("#as-of").textContent = overview.as_of ? `Data through ${overview.as_of}` : "No ClickHouse rows loaded";
  renderSnapshot(data);
  renderSignals(data.signals || []);
  renderPerformance(data.performance || []);
  renderUniverse(data.universe || [], data.technicals || [], data.sentiment || []);
  updateChartSymbolOptions(data.universe || []);
  setActiveTab(state.activeTab);
}

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
  renderPaperList(
    "#paper-accounts",
    paper.accounts || [],
    account => account.name,
    account => `${account.base_currency || "KRW"} ${formatNumber(account.initial_cash)}`
  );
  renderPaperList(
    "#paper-strategies",
    paper.strategies || [],
    strategy => strategy.name,
    strategy => strategySummary(strategy)
  );
  renderPaperList(
    "#paper-backtests",
    paper.backtests || [],
    run => `${formatPercent(run.total_return)} return`,
    run => `${run.start_date || "-"} to ${run.end_date || "-"} · ${formatNumber(run.trade_count)} trades`
  );
}

function renderPaperSelect(selector, rows, valueKey, labelKey) {
  const select = document.querySelector(selector);
  if (!select) return;
  select.innerHTML = rows
    .map(row => `<option value="${row[valueKey]}">${row[labelKey] || row[valueKey]}</option>`)
    .join("");
}

function renderPaperList(selector, rows, title, detail) {
  const container = document.querySelector(selector);
  if (!container) return;
  container.innerHTML = rows.length
    ? rows.map(row => `<article><strong>${title(row)}</strong><span>${detail(row)}</span></article>`).join("")
    : `<article><strong>No rows</strong><span>Create or run one first</span></article>`;
}

function strategySummary(strategy) {
  const config = parseJson(strategy.config_json) || {};
  const patterns = (config.pattern_names || []).join(", ") || "all patterns";
  const indicators = (config.selected_indicators || []).join(", ") || "all indicators";
  return `${patterns} · ${config.min_indicator_agreement || 0}+ votes from ${indicators}`;
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
  const rows = performance
    .map(
      item => `<tr>
        <td>${item.pattern_name}</td>
        <td>${item.horizon_days}D</td>
        <td>${item.htf_trend}</td>
        <td>${tag(item.retest_confirmed)}</td>
        <td>${formatNumber(item.sample_count)}</td>
        <td>${formatPercent(item.target_hit_rate)}</td>
        <td>${formatPercent(item.avg_return_at_horizon)}</td>
        <td>${Number(item.avg_confluence_score || 0).toFixed(2)}</td>
      </tr>`
    )
    .join("");
  document.querySelector("#performance-body").innerHTML = rows || `<tr><td colspan="8">No performance rows found</td></tr>`;
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

function renderUniverse(universe, technicals, sentiment) {
  const features = technicalBySymbol(technicals);
  const sentimentRows = sentimentBySymbol(sentiment);
  const rows = universe
    .map(item => {
      const technical = features.get(item.symbol) || {};
      const sentiment = sentimentRows.get(item.symbol) || {};
      return `<tr data-symbol="${item.symbol}">
        <td>${item.rank}</td>
        <td>${item.symbol}</td>
        <td>${item.name || item.symbol}</td>
        <td>${item.provider}</td>
        <td>${item.universe_name}</td>
        <td>${item.selection_metric_name}</td>
        <td>${formatNumber(item.selection_metric_value)}</td>
        <td>${formatDecimal(technical.vwap)}</td>
        <td>${formatDecimal(technical.ema_20)} / ${formatDecimal(technical.ema_50)}</td>
        <td>${technical.rsi_14 === null || technical.rsi_14 === undefined ? "-" : Number(technical.rsi_14).toFixed(1)}</td>
        <td>${technical.rsi_divergence || "none"}</td>
        <td><span class="sentiment ${sentiment.sentiment_label || "neutral"}">${sentiment.sentiment_label || "neutral"}</span></td>
        <td class="reason-cell" title="${sentiment.latest_reason || ""}">${sentiment.latest_reason || "-"}</td>
      </tr>`;
    })
    .join("");
  document.querySelector("#universe-body").innerHTML = rows || `<tr><td colspan="13">No universe rows found</td></tr>`;
  document.querySelectorAll("#universe-body [data-symbol]").forEach(row => {
    row.addEventListener("click", () => {
      setActiveTab("overview");
      loadSymbolChart(row.dataset.symbol);
    });
  });
}

function updateChartSymbolOptions(universe) {
  const select = document.querySelector("#chart-symbol");
  if (!select) return;
  const current = state.selectedSymbol || select.value;
  select.innerHTML = universe
    .map(item => `<option value="${item.symbol}">${item.symbol} ${item.name || ""}</option>`)
    .join("");
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
  listKeys.forEach(key => {
    payload[key] = formData.getAll(key);
  });
  return payload;
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
document.querySelector("#paper-refresh")?.addEventListener("click", loadPaperDashboard);
document.querySelector("#paper-account-form")?.addEventListener("submit", createPaperAccount);
document.querySelector("#paper-strategy-form")?.addEventListener("submit", createPaperStrategy);
document.querySelector("#paper-backtest-form")?.addEventListener("submit", runPaperBacktest);

initializeSortableTables();
initializeChartInteractions();
loadDashboard();

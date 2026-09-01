(() => {
  'use strict';

  const DATA_BASE = /^(?:localhost|127\.0\.0\.1)$/.test(window.location.hostname)
    ? '.codex-work/sentiment-methodology/'
    : 'https://raw.githubusercontent.com/simonlangley1983/stocklayer/main/';
  const cache = new Map();
  const colours = ['#2563eb', '#0f766e', '#b45309', '#7c3aed', '#be123c'];
  let activeLoadingTimer = null;
  let activeLoadId = 0;

  const escapeHtml = value => String(value ?? '')
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#039;');
  const finite = value => value === null || value === undefined || value === ''
    ? null
    : (Number.isFinite(Number(value)) ? Number(value) : null);
  const fmtDate = value => value
    ? new Intl.DateTimeFormat('en-GB', { day: 'numeric', month: 'short', year: 'numeric' })
      .format(new Date(`${String(value).slice(0, 10)}T12:00:00Z`))
    : 'Unavailable';
  const scoreLabel = value => value == null ? 'Unavailable' : value >= 58 ? 'Positive' : value <= 42 ? 'Negative' : 'Mixed / neutral';
  const scoreClass = value => value == null ? 'report-muted' : value >= 58 ? 'report-positive' : value <= 42 ? 'report-negative' : 'report-neutral';

  async function fetchReport(slug) {
    if (!cache.has(slug)) {
      cache.set(slug, fetch(`${DATA_BASE}company-reports/${encodeURIComponent(slug)}.json?v=${Date.now()}`, { cache: 'no-store' })
        .then(response => {
          if (!response.ok) throw new Error(`Company report returned ${response.status}`);
          return response.json();
        }));
    }
    return cache.get(slug);
  }

  const summaries = new Map();
  async function preloadSummaries() {
    try {
      const response = await fetch(`${DATA_BASE}company-reports/manifest.json?v=${Date.now()}`, { cache: 'no-store' });
      if (!response.ok) return;
      const manifest = await response.json();
      (manifest.companies || []).forEach(item => summaries.set(item.slug, item));
      window.dispatchEvent(new CustomEvent('stocklayer:company-reports-ready'));
    } catch (_) {
      // Individual analysis reports remain available if the compact manifest is temporarily unavailable.
    }
  }

  const getSummary = slug => summaries.get(slug) || null;

  function scoreScale(kind = 'signal') {
    const labels = kind === 'confidence'
      ? ['0 Low', '50 Mixed', '100 Strong']
      : ['0 Negative', '50 Neutral', '100 Positive'];
    return `<div class="report-score-scale" aria-label="${escapeHtml(labels.join(', '))}">${labels.map(label => `<span>${label}</span>`).join('')}</div>`;
  }

  function lineChart(points, options = {}) {
    const values = points.filter(point => point.value != null && point.date);
    if (values.length < 2) return '<p class="report-empty">Not enough scored observations to draw this chart yet.</p>';
    const width = 900, height = 290;
    const pad = { left: options.scoreScale ? 86 : 48, right: 20, top: 24, bottom: 38 };
    const minDate = Math.min(...values.map(point => Date.parse(`${point.date}T12:00:00Z`)));
    const maxDate = Math.max(...values.map(point => Date.parse(`${point.date}T12:00:00Z`)));
    const lower = options.lower ?? Math.max(0, Math.floor(Math.min(...values.map(point => point.value)) / 10) * 10 - 5);
    const upper = options.upper ?? Math.min(100, Math.ceil(Math.max(...values.map(point => point.value)) / 10) * 10 + 5);
    const plotWidth = width - pad.left - pad.right;
    const plotHeight = height - pad.top - pad.bottom;
    const x = date => pad.left + ((Date.parse(`${date}T12:00:00Z`) - minDate) / Math.max(1, maxDate - minDate)) * plotWidth;
    const y = value => pad.top + (1 - (value - lower) / Math.max(1, upper - lower)) * plotHeight;
    const groups = [];
    let active = [];
    points.forEach(point => {
      if (point.value == null || !point.date) {
        if (active.length) groups.push(active);
        active = [];
      } else active.push(point);
    });
    if (active.length) groups.push(active);
    const paths = groups.filter(group => group.length > 1).map(group =>
      `<path class="report-chart-line" d="${group.map((point, index) => `${index ? 'L' : 'M'}${x(point.date).toFixed(1)},${y(point.value).toFixed(1)}`).join(' ')}"/>`
    ).join('');
    const eventMarkers = (options.events || []).filter(event => {
      const time = Date.parse(`${event.date}T12:00:00Z`);
      return time >= minDate && time <= maxDate;
    }).map((event, index) => {
      const eventX = x(event.date);
      const markerY = pad.top + 14 + (index % 2) * 22;
      return `<g class="report-event-marker"><line x1="${eventX}" y1="${pad.top}" x2="${eventX}" y2="${pad.top + plotHeight}"/><circle cx="${eventX}" cy="${markerY}" r="10"><title>Event ${index + 1}: ${escapeHtml(event.title)}</title></circle><text x="${eventX}" y="${markerY + 4}" text-anchor="middle">${index + 1}</text></g>`;
    }).join('');
    const ticks = [upper, (upper + lower) / 2, lower].map(value => {
      const tickY = y(value);
      const label = options.scoreScale
        ? (value === 100 ? '100 Positive' : value === 50 ? '50 Neutral' : '0 Negative')
        : Math.round(value);
      return `<line class="report-gridline" x1="${pad.left}" y1="${tickY}" x2="${width - pad.right}" y2="${tickY}"/><text x="4" y="${tickY + 4}">${label}</text>`;
    }).join('');
    const start = values[0].date, end = values.at(-1).date;
    return `<svg class="company-report-chart" viewBox="0 0 ${width} ${height}" role="img" aria-label="${escapeHtml(options.label || 'Score over time')}">${ticks}${eventMarkers}${paths}<text x="${pad.left}" y="${height - 8}">${escapeHtml(fmtDate(start))}</text><text x="${width - pad.right}" y="${height - 8}" text-anchor="end">${escapeHtml(fmtDate(end))}</text></svg>`;
  }

  function multiLineAnnualChart(reports) {
    if (reports.length < 2) return '<p class="report-empty">A second extracted annual report is required before changes over time can be calculated.</p>';
    const keywords = reports.at(-1).keywords
      .map(keyword => ({ ...keyword, change: keyword.per10kWords - (reports.at(-2).keywords.find(item => item.key === keyword.key)?.per10kWords || 0) }))
      .sort((a, b) => Math.abs(b.change) - Math.abs(a.change)).slice(0, 4);
    const points = reports.flatMap(report => keywords.map(keyword => ({ year: report.year, key: keyword.key, label: keyword.label, value: report.keywords.find(item => item.key === keyword.key)?.per10kWords || 0 })));
    const max = Math.max(1, ...points.map(point => point.value));
    const width = 900, height = 300, left = 70, right = 20, top = 25, bottom = 42;
    const x = index => left + index * ((width - left - right) / Math.max(1, reports.length - 1));
    const y = value => top + (1 - value / max) * (height - top - bottom);
    const paths = keywords.map((keyword, colourIndex) => {
      const series = reports.map((report, index) => ({ index, value: report.keywords.find(item => item.key === keyword.key)?.per10kWords || 0 }));
      return `<path style="--series-colour:${colours[colourIndex]}" class="report-keyword-line" d="${series.map((point, index) => `${index ? 'L' : 'M'}${x(point.index).toFixed(1)},${y(point.value).toFixed(1)}`).join(' ')}"/>${series.map(point => `<circle class="report-keyword-point" style="--series-colour:${colours[colourIndex]}" cx="${x(point.index)}" cy="${y(point.value)}" r="4"/>`).join('')}`;
    }).join('');
    return `<svg class="company-report-chart" viewBox="0 0 ${width} ${height}" role="img" aria-label="Annual report keyword rates over time"><line class="report-gridline" x1="${left}" y1="${height - bottom}" x2="${width - right}" y2="${height - bottom}"/>${paths}${reports.map((report, index) => `<text x="${x(index)}" y="${height - 12}" text-anchor="middle">${report.year}</text>`).join('')}<text x="4" y="${top + 4}">${max.toFixed(1)}</text><text x="4" y="${height - bottom + 4}">0</text><text class="report-axis-title" x="4" y="${top + 22}">mentions / 10k words</text></svg><div class="report-legend">${keywords.map((keyword, index) => `<span><i style="--series-colour:${colours[index]}"></i>${escapeHtml(keyword.label)}</span>`).join('')}</div><p class="report-chart-note">Coloured points are measurements from each report year.</p>`;
  }

  function timelineEvents(events, limit = 6) {
    return [...events].filter(event => event.type !== 'annual_report').sort((a, b) => a.date.localeCompare(b.date)).slice(-limit);
  }

  function eventList(events) {
    if (!events.length) return '<p class="report-empty">No evidenced press events are available yet.</p>';
    return `<ol class="company-report-events">${events.map((event, index) => `<li><span class="report-event-number">${index + 1}</span><time>${escapeHtml(fmtDate(event.date))}</time><div><strong>${escapeHtml(event.title)}</strong>${event.detail ? `<p>${escapeHtml(event.detail)}</p>` : ''}</div>${event.url ? `<a class="report-source-link" href="${escapeHtml(event.url)}" target="_blank" rel="noopener noreferrer">Source ↗</a>` : ''}</li>`).join('')}</ol>`;
  }

  function signalComparison(confidence) {
    const components = confidence?.components || [];
    if (!components.length) return '<p class="report-empty">Measured signals are not available yet.</p>';
    return `<div class="report-signal-comparison">${components.map(item => `<div class="report-signal-row"><div><strong>${escapeHtml(item.label)}</strong><small>${escapeHtml(item.detail || '')}</small></div><div class="report-signal-bar"><i style="width:${Math.max(0, Math.min(100, Number(item.score)))}%"></i><b class="report-neutral-marker"></b></div><strong class="${scoreClass(item.score)}">${Number(item.score).toFixed(1)}<span>/100</span></strong></div>`).join('')}</div>${scoreScale('signal')}`;
  }

  function sectionOne(report, company) {
    const item = report.company;
    const richerIntro = company?.usp
      || company?.companyUsp
      || company?.uniqueSellingPoint
      || (typeof window.getCompanyUsp === 'function' ? window.getCompanyUsp(company) : '')
      || item.introduction;
    return `<section class="company-report-section company-report-overview" aria-labelledby="report-section-company"><header><h3 id="report-section-company">Company overview</h3></header><div class="company-report-profile"><div><p class="company-report-intro">${escapeHtml(richerIntro || item.introduction)}</p><dl><div><dt>Company type</dt><dd>${escapeHtml(item.sector || 'Unavailable')}</dd></div><div><dt>Listing</dt><dd>${escapeHtml(item.ticker || 'Unavailable')}</dd></div><div><dt>UK 100 rank</dt><dd>${item.ftseRank ? `#${item.ftseRank}` : 'Unavailable'}</dd></div><div><dt>Market value</dt><dd>${escapeHtml(item.marketCap || 'Unavailable')}</dd></div></dl></div></div></section>`;
  }

  function sectionTwo(report) {
    const confidence = report.overallConfidence || {};
    return `<section class="company-report-section report-confidence-summary" aria-labelledby="report-section-confidence"><header><h3 id="report-section-confidence">Overall confidence</h3><p>One comparable score built from the signals shown below.</p></header><div class="report-confidence-hero"><div class="report-confidence-score ${scoreClass(confidence.score)}"><strong>${confidence.score == null ? '—' : Math.round(Number(confidence.score))}</strong><span>/100</span><small>${escapeHtml(confidence.label || 'Unavailable')}</small></div><div><div class="report-overall-bar"><i style="width:${Math.max(0, Math.min(100, Number(confidence.score) || 0))}%"></i><b></b></div>${scoreScale('confidence')}<p>${escapeHtml(confidence.methodology || '')}</p><small>Evidence coverage: ${Number(confidence.evidenceCoverage || 0)}%. This measures how much reliable source data supports the score, not whether the outlook is positive.</small></div></div><h4>What drives the score</h4>${signalComparison(confidence)}</section>`;
  }

  function sectionThree(report) {
    const press = report.pressCoverage;
    const annual = report.annualReportAnalysis;
    const pressSeries = press.series.map(item => ({ date: item.date, value: finite(item.dailyScore) }));
    const annualSeries = annual.reports.map(item => ({ date: `${item.year}-12-31`, value: finite(item.positivityScore) }));
    const events = timelineEvents(report.events, 6);
    return `<section class="company-report-section" aria-labelledby="report-section-trends"><header><h3 id="report-section-trends">Signals over time</h3><p>Both charts use the same 0–100 scale, so direction is directly comparable. The time periods remain separate because press is daily and reports are annual.</p></header><div class="company-report-chart-grid"><div class="company-report-chart-card"><div><h4>Press sentiment</h4><p>Numbered markers match the evidenced events immediately below. Gaps mean no eligible coverage.</p></div>${lineChart(pressSeries, { lower: 0, upper: 100, scoreScale: true, label: 'Daily press sentiment with numbered events', events })}</div><div class="company-report-chart-card"><div><h4>Annual-report tone</h4><p>${escapeHtml(annual.latestPositivityMethod || 'Awaiting sufficient reports')}.</p></div>${lineChart(annualSeries, { lower: 0, upper: 100, scoreScale: true, label: 'Annual report positivity over time' })}</div></div><div class="company-report-subsection"><h4>Events behind the press signal</h4>${eventList(events)}</div></section>`;
  }

  function sectionFour(report) {
    const annual = report.annualReportAnalysis;
    const emerging = annual.emergingKeywords.filter(item => item.changePer10kWords > 0);
    const increases = emerging.slice(0, 5);
    const largestChange = Math.max(0.01, ...increases.map(item => Number(item.changePer10kWords)));
    const changeRows = increases.map(item => {
      const change = Number(item.changePer10kWords);
      const width = Math.max(4, change / largestChange * 100);
      return `<div class="report-theme-change"><strong>${escapeHtml(item.label)}</strong><div class="report-theme-bar" aria-hidden="true"><i style="width:${width.toFixed(1)}%"></i></div><span><b>+${change.toFixed(2)}</b> per 10,000 words</span></div>`;
    }).join('');
    return `<section class="company-report-section" aria-labelledby="report-section-themes"><header><h3 id="report-section-themes">Annual-report themes</h3><p>Keyword rates are normalized per 10,000 report words, making reports of different lengths comparable.</p></header><div class="company-report-chart-card">${multiLineAnnualChart(annual.reports)}</div><div class="company-report-subsection"><h4>Largest increases in the latest report</h4>${increases.length ? `<div class="report-theme-changes">${changeRows}</div>` : '<p class="report-empty">A second report is needed to calculate emerging themes.</p>'}</div><p class="report-theme-note">Change versus the previous report, measured as additional mentions per 10,000 extracted words.</p></section>`;
  }

  function render(report, company) {
    return `<div class="company-report"><nav class="company-report-nav" aria-label="Report sections"><a href="#report-section-company">Overview</a><a href="#report-section-confidence">Confidence</a><a href="#report-section-trends">Trends & events</a><a href="#report-section-themes">Themes</a></nav>${sectionOne(report, company)}${sectionTwo(report)}${sectionThree(report)}${sectionFour(report)}<p class="company-report-disclaimer">StockLayer indicators are descriptive research signals, not financial advice. Source links and methodology notes are provided so the evidence can be checked.</p></div>`;
  }

  async function open({ company, modal, title, subtitle, content }) {
    const name = company.companyName || company.ticker || 'Company';
    const loadId = ++activeLoadId;
    if (activeLoadingTimer) window.clearInterval(activeLoadingTimer);
    title.textContent = `${name} intelligence`;
    subtitle.textContent = 'Building your intelligence report...';
    content.setAttribute('aria-busy', 'true');
    content.innerHTML = `
      <div class="intel-ai-loading" role="status" aria-live="polite">
        <div class="intel-ai-loading-icon" aria-hidden="true">
          <img src="images/stocklayer-icon-new.png?v=20260803-new" alt="">
        </div>
        <strong>Building intelligence report</strong>
        <span>Preparing StockLayer's research signals.</span>
        <ul class="intel-loading-steps" aria-label="Intelligence report progress">
          <li class="intel-loading-step is-active">Loading annual reports...</li>
          <li class="intel-loading-step">Analysing report keywords...</li>
          <li class="intel-loading-step">Checking market performance...</li>
          <li class="intel-loading-step">Building growth confidence...</li>
        </ul>
      </div>`;
    modal.hidden = false;
    modal.querySelector('.stocklayer-intel-modal')?.scrollTo(0, 0);
    let loadingStepIndex = 0;
    const loadingTimer = window.setInterval(() => {
      const steps = Array.from(content.querySelectorAll('.intel-loading-step'));
      if (!steps.length) return;
      loadingStepIndex = Math.min(loadingStepIndex + 1, steps.length - 1);
      steps.forEach((step, index) => {
        step.classList.toggle('is-complete', index < loadingStepIndex);
        step.classList.toggle('is-active', index === loadingStepIndex);
      });
    }, 1600);
    activeLoadingTimer = loadingTimer;
    try {
      const [report] = await Promise.all([
        fetchReport(company.slug),
        new Promise(resolve => window.setTimeout(resolve, 7000))
      ]);
      if (loadId !== activeLoadId || modal.hidden) return;
      title.textContent = `${name} intelligence report`;
      subtitle.textContent = 'One confidence score, its measured signals and the evidence behind them.';
      content.innerHTML = render(report, company);
      content.removeAttribute('aria-busy');
    } catch (error) {
      if (loadId !== activeLoadId || modal.hidden) return;
      content.innerHTML = `<div class="company-report-error"><strong>Report data is temporarily unavailable.</strong><p>${escapeHtml(error.message)}</p></div>`;
      content.removeAttribute('aria-busy');
    } finally {
      window.clearInterval(loadingTimer);
      if (activeLoadingTimer === loadingTimer) activeLoadingTimer = null;
    }
  }

  window.StockLayerCompanyReport = { open, render, lineChart, multiLineAnnualChart, getSummary };
  preloadSummaries();
})();

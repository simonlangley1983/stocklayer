(() => {
  'use strict';

  const DATA_BASE = /^(?:localhost|127\.0\.0\.1)$/.test(window.location.hostname)
    ? '.codex-work/sentiment-methodology/'
    : 'https://raw.githubusercontent.com/simonlangley1983/stocklayer/main/';
  const cache = new Map();
  const colours = ['#2563eb', '#0f766e', '#b45309', '#7c3aed', '#be123c'];

  const escapeHtml = value => String(value ?? '')
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#039;');
  const finite = value => Number.isFinite(Number(value)) ? Number(value) : null;
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

  function lineChart(points, options = {}) {
    const values = points.filter(point => point.value != null && point.date);
    if (values.length < 2) return '<p class="report-empty">Not enough scored observations to draw this chart yet.</p>';
    const width = 900, height = 290;
    const pad = { left: 48, right: 20, top: 24, bottom: 38 };
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
      return `<g class="report-event-marker"><line x1="${eventX}" y1="${pad.top}" x2="${eventX}" y2="${pad.top + plotHeight}"/><circle cx="${eventX}" cy="${pad.top + 10 + (index % 3) * 16}" r="5"><title>${escapeHtml(event.title)}</title></circle></g>`;
    }).join('');
    const ticks = [upper, (upper + lower) / 2, lower].map(value => {
      const tickY = y(value);
      return `<line class="report-gridline" x1="${pad.left}" y1="${tickY}" x2="${width - pad.right}" y2="${tickY}"/><text x="6" y="${tickY + 4}">${Math.round(value)}</text>`;
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
    const width = 900, height = 300, left = 48, right = 20, top = 25, bottom = 42;
    const x = index => left + index * ((width - left - right) / Math.max(1, reports.length - 1));
    const y = value => top + (1 - value / max) * (height - top - bottom);
    const paths = keywords.map((keyword, colourIndex) => {
      const series = reports.map((report, index) => ({ index, value: report.keywords.find(item => item.key === keyword.key)?.per10kWords || 0 }));
      return `<path style="--series-colour:${colours[colourIndex]}" class="report-keyword-line" d="${series.map((point, index) => `${index ? 'L' : 'M'}${x(point.index).toFixed(1)},${y(point.value).toFixed(1)}`).join(' ')}"/>${series.map(point => `<circle fill="${colours[colourIndex]}" cx="${x(point.index)}" cy="${y(point.value)}" r="4"/>`).join('')}`;
    }).join('');
    return `<svg class="company-report-chart" viewBox="0 0 ${width} ${height}" role="img" aria-label="Annual report keyword rates over time"><line class="report-gridline" x1="${left}" y1="${height - bottom}" x2="${width - right}" y2="${height - bottom}"/>${paths}${reports.map((report, index) => `<text x="${x(index)}" y="${height - 12}" text-anchor="middle">${report.year}</text>`).join('')}<text x="6" y="${top + 4}">${max.toFixed(1)}</text><text x="6" y="${height - bottom + 4}">0</text></svg><div class="report-legend">${keywords.map((keyword, index) => `<span><i style="--series-colour:${colours[index]}"></i>${escapeHtml(keyword.label)}</span>`).join('')}</div>`;
  }

  function eventList(events, limit = 8) {
    const recent = [...events].filter(event => event.type !== 'annual_report').sort((a, b) => b.date.localeCompare(a.date)).slice(0, limit);
    if (!recent.length) return '<p class="report-empty">No evidenced press events are available yet.</p>';
    return `<ol class="company-report-events">${recent.map(event => `<li><time>${escapeHtml(fmtDate(event.date))}</time><div><strong>${escapeHtml(event.title)}</strong>${event.detail ? `<p>${escapeHtml(event.detail)}</p>` : ''}${event.url ? `<a href="${escapeHtml(event.url)}" target="_blank" rel="noopener noreferrer">Read source</a>` : ''}</div></li>`).join('')}</ol>`;
  }

  function sectionOne(report, company) {
    const item = report.company;
    const richerIntro = company?.usp
      || company?.companyUsp
      || company?.uniqueSellingPoint
      || (typeof window.getCompanyUsp === 'function' ? window.getCompanyUsp(company) : '')
      || item.introduction;
    return `<section class="company-report-section" aria-labelledby="report-section-company"><header><span>Section 1</span><h3 id="report-section-company">About the company</h3></header><div class="company-report-profile"><div><p class="company-report-intro">${escapeHtml(richerIntro || item.introduction)}</p><dl><div><dt>Company type</dt><dd>${escapeHtml(item.sector || 'Unavailable')}</dd></div><div><dt>Listing</dt><dd>${escapeHtml(item.ticker || 'Unavailable')}</dd></div><div><dt>UK 100 rank</dt><dd>${item.ftseRank ? `#${item.ftseRank}` : 'Unavailable'}</dd></div><div><dt>Market value</dt><dd>${escapeHtml(item.marketCap || 'Unavailable')}</dd></div></dl></div></div></section>`;
  }

  function sectionTwo(report) {
    const press = report.pressCoverage;
    const series = press.series.map(item => ({ date: item.date, value: finite(item.dailyScore) }));
    return `<section class="company-report-section" aria-labelledby="report-section-press"><header><span>Section 2</span><h3 id="report-section-press">Press coverage and daily sentiment</h3></header><div class="company-report-stat-grid"><div><span>Latest scored day</span><strong class="${scoreClass(press.latestScore)}">${press.latestScore == null ? 'No coverage' : Number(press.latestScore).toFixed(1)}</strong><small>${escapeHtml(fmtDate(press.latestScoreDate))}</small></div><div><span>Direction</span><strong>${escapeHtml(scoreLabel(press.latestScore))}</strong></div><div><span>Scored days</span><strong>${press.scoredDayCount}/${press.observationCount}</strong></div></div><div class="company-report-chart-card"><div><h4>Daily press sentiment</h4><p>0 is most negative, 50 neutral and 100 most positive. Missing coverage is shown as a gap.</p></div>${lineChart(series, { lower: 0, upper: 100, label: 'Daily press sentiment score', events: press.stories })}</div><div class="company-report-subsection"><h4>Key press coverage</h4>${eventList(press.stories, 6)}</div></section>`;
  }

  function sectionThree(report) {
    const annual = report.annualReportAnalysis;
    const latest = annual.reports.at(-1);
    const emerging = annual.emergingKeywords.filter(item => item.changePer10kWords > 0);
    return `<section class="company-report-section" aria-labelledby="report-section-annual"><header><span>Section 3</span><h3 id="report-section-annual">Annual-report changes</h3></header><div class="company-report-stat-grid"><div><span>Reports extracted</span><strong>${annual.reportCount}</strong></div><div><span>Latest report</span><strong>${latest?.year || 'Unavailable'}</strong></div><div><span>Overall positivity</span><strong class="${scoreClass(annual.latestPositivity)}">${annual.latestPositivity == null ? 'Needs two reports' : `${Number(annual.latestPositivity).toFixed(1)}/100`}</strong><small>${escapeHtml(annual.latestPositivityMethod || '')}</small></div></div><div class="company-report-chart-card"><div><h4>Emerging annual-report themes</h4><p>Mentions are normalized per 10,000 report words so differently sized reports remain comparable.</p></div>${multiLineAnnualChart(annual.reports)}</div><div class="company-report-subsection"><h4>Largest emerging keywords</h4>${emerging.length ? `<div class="company-report-keywords">${emerging.slice(0, 5).map(item => `<div><span>${escapeHtml(item.label)}</span><strong>+${Number(item.changePer10kWords).toFixed(2)}</strong><small>mentions per 10k words</small></div>`).join('')}</div>` : '<p class="report-empty">No positive keyword increases can be calculated yet.</p>'}</div><p class="company-report-method">${escapeHtml(annual.methodology)}</p></section>`;
  }

  function sectionFour(report) {
    const pressSeries = report.pressCoverage.series.map(item => ({ date: item.date, value: finite(item.dailyScore) }));
    const annualSeries = report.annualReportAnalysis.reports.map(item => ({ date: `${item.year}-12-31`, value: finite(item.positivityScore) }));
    const pressEvents = report.events.filter(item => item.type !== 'annual_report');
    const annualEvents = report.events.filter(item => item.type === 'annual_report');
    return `<section class="company-report-section" aria-labelledby="report-section-events"><header><span>Section 4</span><h3 id="report-section-events">Signals plotted against key events</h3></header><p class="company-report-lead">The two time horizons are kept separate to avoid stretching recent daily press data across several annual-report years.</p><div class="company-report-chart-card"><div><h4>Recent press sentiment and events</h4></div>${lineChart(pressSeries, { lower: 0, upper: 100, label: 'Press sentiment with key-event markers', events: pressEvents })}</div><div class="company-report-chart-card"><div><h4>Annual-report positivity and report years</h4></div>${lineChart(annualSeries, { lower: 0, upper: 100, label: 'Annual report positivity with report-year markers', events: annualEvents })}</div><div class="company-report-subsection"><h4>Event evidence</h4>${eventList(pressEvents, 10)}</div></section>`;
  }

  function render(report, company) {
    return `<div class="company-report"><nav class="company-report-nav" aria-label="Report sections"><a href="#report-section-company">Company</a><a href="#report-section-press">Press</a><a href="#report-section-annual">Annual reports</a><a href="#report-section-events">Events</a></nav>${sectionOne(report, company)}${sectionTwo(report)}${sectionThree(report)}${sectionFour(report)}<p class="company-report-disclaimer">StockLayer indicators are descriptive research signals, not financial advice. Source links and methodology notes are provided so the evidence can be checked.</p></div>`;
  }

  async function open({ company, modal, title, subtitle, content }) {
    const name = company.companyName || company.ticker || 'Company';
    title.textContent = `${name} intelligence report`;
    subtitle.textContent = 'Company profile, press sentiment, annual reports and key events.';
    content.setAttribute('aria-busy', 'true');
    content.innerHTML = '<div class="company-report-loading" role="status"><span></span><strong>Loading evidenced company report…</strong></div>';
    modal.hidden = false;
    modal.querySelector('.stocklayer-intel-modal')?.scrollTo(0, 0);
    try {
      const report = await fetchReport(company.slug);
      if (modal.hidden) return;
      content.innerHTML = render(report, company);
      content.removeAttribute('aria-busy');
    } catch (error) {
      content.innerHTML = `<div class="company-report-error"><strong>Report data is temporarily unavailable.</strong><p>${escapeHtml(error.message)}</p></div>`;
      content.removeAttribute('aria-busy');
    }
  }

  window.StockLayerCompanyReport = { open, render, lineChart, multiLineAnnualChart };
})();


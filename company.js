// ============================================
// StockLayer - Company Dashboard
// ============================================

const DATA_BASE_URL = 'https://raw.githubusercontent.com/simonlangley1983/stocklayer/main/';
const LOGO_DEV_TOKEN = 'pk_TcZyT9v_RYK_XhbFvnsv8w';
const MAX_ACQUISITION_PRICE_DISTANCE_DAYS = 21;

const params = new URLSearchParams(window.location.search);
const stock = params.get('stock');

let acquisitionState = {
    acquisitions: null,
    history: [],
    company: null,
    sortMode: 'date-desc'
};

let strategicIntelligenceState = {
    data: null,
    metadata: null,
    category: 'All',
    sortMode: 'useful'
};

function getLogoUrl(company, size = 128) {
    const domain = company.domain || company.logoDomain || company.websiteDomain || normaliseDomain(company.website || company.url || '');
    if (!domain) return '';
    return `https://img.logo.dev/${domain}?token=${LOGO_DEV_TOKEN}&size=${size}`;
}

function normaliseDomain(value) {
    if (!value) return '';

    try {
        const url = String(value).startsWith('http') ? new URL(value) : new URL(`https://${value}`);
        return url.hostname.replace(/^www\./, '');
    } catch (error) {
        return String(value).replace(/^https?:\/\//, '').replace(/^www\./, '').split('/')[0];
    }
}

function companyInitials(company, stockSlug) {
    const name = company.companyName || company.name || company.ticker || stockSlug || '?';
    return name
        .split(/\s+/)
        .filter(Boolean)
        .slice(0, 2)
        .map(part => part[0])
        .join('')
        .toUpperCase();
}

function formatCurrency(value, currency = 'USD') {
    if (value === undefined || value === null || value === '') return '-';

    const n = Number(value);

    if (!Number.isNaN(n)) {
        return new Intl.NumberFormat('en-US', {
            style: 'currency',
            currency: currency || 'USD',
            maximumFractionDigits: n >= 1000 ? 0 : 2
        }).format(n);
    }

    return value;
}

function formatMarketCap(value) {
    if (value === undefined || value === null || value === '') return '-';
    const text = value.toString().trim();
    if (text.startsWith('$')) return text;
    return `$${text}`;
}

function formatPercent(value) {
    if (value === undefined || value === null || value === '') return '-';
    const n = Number(value);
    if (Number.isNaN(n)) return `${value}%`;
    const sign = n > 0 ? '+' : '';
    return `${sign}${n.toFixed(1)}%`;
}

function formatNumber(value) {
    if (value === undefined || value === null || value === '') return '-';
    const n = Number(value);
    if (Number.isNaN(n)) return value;
    return n.toLocaleString();
}

function formatDate(value) {
    if (!value) return '-';
    const text = String(value).trim();

    if (/^\d{2}\/\d{2}\/\d{4}$/.test(text)) return text;

    const isoMatch = text.match(/^(\d{4})-(\d{2})-(\d{2})/);
    if (isoMatch) return `${isoMatch[3]}/${isoMatch[2]}/${isoMatch[1]}`;

    const date = new Date(text);
    if (!Number.isNaN(date.getTime())) {
        return new Intl.DateTimeFormat('en-GB', {
            day: '2-digit',
            month: '2-digit',
            year: 'numeric'
        }).format(date);
    }

    return text;
}

function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>'"]/g, character => ({
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        "'": '&#39;',
        '"': '&quot;'
    }[character]));
}

function safeExternalUrl(value) {
    try {
        const url = new URL(String(value));
        return ['http:', 'https:'].includes(url.protocol) ? url.href : '';
    } catch (error) {
        return '';
    }
}

function parseDate(value) {
    if (!value) return null;
    const text = String(value).trim();

    const uk = text.match(/^(\d{2})\/(\d{2})\/(\d{4})$/);
    if (uk) return new Date(`${uk[3]}-${uk[2]}-${uk[1]}T00:00:00`);

    const iso = text.match(/^(\d{4})-(\d{2})-(\d{2})/);
    if (iso) return new Date(`${iso[1]}-${iso[2]}-${iso[3]}T00:00:00`);

    const d = new Date(text);
    return Number.isNaN(d.getTime()) ? null : d;
}

function daysBetween(a, b) {
    return Math.abs(a.getTime() - b.getTime()) / (1000 * 60 * 60 * 24);
}

function addMonths(date, months) {
    const copy = new Date(date.getTime());
    copy.setMonth(copy.getMonth() + months);
    return copy;
}

function parseCostToBillions(value) {
    if (value === undefined || value === null || value === '') return null;

    const text = String(value).toLowerCase().replace(/,/g, '').trim();

    if (text.includes('undisclosed') || text.includes('unknown') || text.includes('pending')) {
        return null;
    }

    const match = text.match(/([0-9]+(?:\.[0-9]+)?)/);
    if (!match) return null;

    const number = Number(match[1]);
    if (Number.isNaN(number)) return null;

    if (text.includes('bn') || text.includes('billion')) return number;
    if (text.includes('m') || text.includes('million')) return number / 1000;

    return number;
}

function setText(id, value) {
    const el = document.getElementById(id);
    if (el) el.textContent = value || '-';
}

function renderList(id, items) {
    const el = document.getElementById(id);
    if (!el) return;

    el.innerHTML = '';

    if (!items || !items.length) {
        el.innerHTML = '<li>No data yet</li>';
        return;
    }

    items.forEach(item => {
        const li = document.createElement('li');
        li.textContent = item;
        el.appendChild(li);
    });
}

function scoreBar(score) {
    const safeScore = Math.max(0, Math.min(10, Number(score) || 0));
    return `
        <div class="score-bar" aria-label="Score ${safeScore} out of 10">
            <div class="score-fill" style="width:${safeScore * 10}%"></div>
        </div>
    `;
}

async function fetchJson(path, fallback = null) {
    const response = await fetch(`${DATA_BASE_URL}${path}?v=${Date.now()}`, {
        cache: 'no-store'
    });

    if (!response.ok) {
        if (fallback !== null) return fallback;
        throw new Error(`${path} returned ${response.status}`);
    }

    const text = await response.text();

    if (!text || !text.trim()) {
        if (fallback !== null) return fallback;
        throw new Error(`${path} is empty`);
    }

    try {
        return JSON.parse(text);
    } catch (error) {
        if (fallback !== null) return fallback;
        throw new Error(`${path} contains invalid JSON`);
    }
}

function normaliseHistory(historyData) {
    const raw = Array.isArray(historyData)
        ? historyData
        : (historyData.prices || historyData.history || []);

    return raw
        .map(item => ({
            date: parseDate(item.date),
            close: Number(item.close ?? item.price ?? item.adjustedClose ?? item.adjClose)
        }))
        .filter(item => item.date && !Number.isNaN(item.close))
        .sort((a, b) => a.date - b.date);
}

function getPriceDate(company) {
    return parseDate(
        company.priceDate ||
        company.lastPriceDate ||
        company.lastUpdated ||
        company.updatedAt ||
        company.updateDate
    ) || new Date();
}

function getBestEverPrice(history, company) {
    const points = [];

    history.forEach(item => {
        if (item.date && item.close && !Number.isNaN(item.close)) {
            points.push({
                price: item.close,
                date: item.date
            });
        }
    });

    const currentPrice = Number(company.currentPrice);
    const currentDate = getPriceDate(company);

    if (!Number.isNaN(currentPrice) && currentPrice > 0) {
        points.push({
            price: currentPrice,
            date: currentDate
        });
    }

    if (!points.length) return null;

    return points.sort((a, b) => b.price - a.price)[0];
}

function renderPriceInsight(company, history) {
    const currentPrice = Number(company.currentPrice);
    const currentDate = getPriceDate(company);
    const bestEver = getBestEverPrice(history, company);

    setText('current-price', formatCurrency(company.currentPrice, company.currency));
    setText('current-price-date', `Latest price date: ${formatDate(currentDate)}`);

    if (!bestEver || Number.isNaN(currentPrice) || currentPrice <= 0) {
        setText('best-ever-price', '-');
        setText('best-ever-price-date', '-');
        setText('current-vs-best', 'Best ever comparison unavailable');
        return;
    }

    const difference = currentPrice - bestEver.price;
    const belowPercent = ((bestEver.price - currentPrice) / bestEver.price) * 100;

    setText('best-ever-price', formatCurrency(bestEver.price, company.currency));
    setText('best-ever-price-date', formatDate(bestEver.date));

    if (difference >= 0) {
        setText('current-vs-best', 'Current price is the best ever price');
    } else {
        setText(
            'current-vs-best',
            `${formatCurrency(Math.abs(difference), company.currency)} (${belowPercent.toFixed(1)}%) below best ever`
        );
    }
}

function findClosestPriceOnOrBefore(history, targetDate) {
    if (!history.length || !targetDate) return null;
    let best = null;
    history.forEach(item => {
        if (item.date <= targetDate) best = item;
    });
    return best || null;
}

function findClosestPriceOnOrAfter(history, targetDate) {
    if (!history.length || !targetDate) return null;
    return history.find(item => item.date >= targetDate) || null;
}

function findClosestPrice(history, targetDate, maxDistanceDays = null) {
    const before = findClosestPriceOnOrBefore(history, targetDate);
    const after = findClosestPriceOnOrAfter(history, targetDate);

    let best = null;

    if (before && after) {
        best = daysBetween(before.date, targetDate) <= daysBetween(after.date, targetDate) ? before : after;
    } else {
        best = before || after;
    }

    if (!best) return null;

    if (maxDistanceDays !== null && daysBetween(best.date, targetDate) > maxDistanceDays) {
        return null;
    }

    return best;
}

function calculateReturnFromPrices(startPrice, endPrice) {
    if (!startPrice || !endPrice) return null;
    return ((endPrice - startPrice) / startPrice) * 100;
}

function calculateAcquisitionReturns(acquisition, history, company) {
    const acquisitionDate = parseDate(acquisition.acquisitionDate);
    const currentPrice = Number(company.currentPrice);

    if (!acquisitionDate || !history.length) return null;

    const startPoint = findClosestPrice(history, acquisitionDate, MAX_ACQUISITION_PRICE_DISTANCE_DAYS);
    if (!startPoint || !startPoint.close) return null;

    const points = [
        { key: '1m', label: '1m', date: addMonths(acquisitionDate, 1) },
        { key: '6m', label: '6m', date: addMonths(acquisitionDate, 6) },
        { key: '12m', label: '12m', date: addMonths(acquisitionDate, 12) }
    ];

    const results = points.map(point => {
        const endPoint = findClosestPrice(history, point.date, MAX_ACQUISITION_PRICE_DISTANCE_DAYS);
        return {
            label: point.label,
            value: endPoint ? calculateReturnFromPrices(startPoint.close, endPoint.close) : null,
            date: endPoint ? endPoint.date : null
        };
    });

    results.push({
        label: 'Now',
        value: currentPrice ? calculateReturnFromPrices(startPoint.close, currentPrice) : null,
        date: new Date()
    });

    return {
        acquisitionDate,
        startPrice: startPoint.close,
        startDate: startPoint.date,
        results
    };
}

function calculateInvestmentValue(history, currentPrice, daysAgo, amount = 1000) {
    if (!history.length || !currentPrice) return null;

    const targetDate = new Date();
    targetDate.setDate(targetDate.getDate() - daysAgo);

    const startPoint = findClosestPriceOnOrBefore(history, targetDate);
    if (!startPoint || !startPoint.close) return null;

    const shares = amount / startPoint.close;
    const currentValue = shares * currentPrice;
    const gain = currentValue - amount;
    const gainPercent = (gain / amount) * 100;

    return { startDate: startPoint.date, startPrice: startPoint.close, currentValue, gain, gainPercent };
}

function renderInvestmentTool(history, company) {
    const el = document.getElementById('investment-tool');
    if (!el) return;

    const currentPrice = Number(company.currentPrice);

    const periods = [
        { label: '1 week ago', days: 7 },
        { label: '1 month ago', days: 30 },
        { label: '3 months ago', days: 91 },
        { label: '6 months ago', days: 183 },
        { label: '12 months ago', days: 365 }
    ];

    if (!history.length || !currentPrice) {
        el.innerHTML = '<p>Historical price data is not available yet.</p>';
        return;
    }

    el.innerHTML = '';

    periods.forEach(period => {
        const result = calculateInvestmentValue(history, currentPrice, period.days, 1000);
        const card = document.createElement('div');
        card.className = 'investment-card';

        if (!result) {
            card.innerHTML = `<h3>${period.label}</h3><p>No data available</p>`;
        } else {
            const positive = result.gain >= 0;
            card.innerHTML = `
                <h3>${period.label}</h3>
                <div class="investment-value">${formatCurrency(result.currentValue, company.currency)}</div>
                <div class="${positive ? 'positive' : 'negative'}">
                    ${positive ? '+' : ''}${formatCurrency(result.gain, company.currency)}
                    (${formatPercent(result.gainPercent)})
                </div>
                <p class="muted">
                    Start price: ${formatCurrency(result.startPrice, company.currency)}
                    on ${formatDate(result.startDate)}
                </p>
            `;
        }

        el.appendChild(card);
    });
}

function calculateCeoSharePricePerformance(leadership, history, company) {
    const currentPrice = Number(company.currentPrice);

    const explicitStartPrice = Number(
        leadership.sharePriceAtStart ??
        leadership.ceoStartPrice ??
        leadership.startSharePrice
    );

    let startPrice = !Number.isNaN(explicitStartPrice) && explicitStartPrice > 0 ? explicitStartPrice : null;
    let startDate = parseDate(leadership.ceoSince);

    if (!startPrice && startDate && history.length) {
        const startPoint = findClosestPriceOnOrBefore(history, startDate);
        if (startPoint) {
            startPrice = startPoint.close;
            startDate = startPoint.date;
        }
    }

    if (!startPrice || !currentPrice) return null;

    const improvement = ((currentPrice - startPrice) / startPrice) * 100;
    return { startDate, startPrice, currentPrice, improvement };
}

function findRiskMarker(markers, searchTerm) {
    return (markers || []).find(marker =>
        (marker.category || '').toLowerCase().includes(searchTerm.toLowerCase())
    );
}

function getNumericField(company, fieldName) {
    const value = company[fieldName];
    if (value === undefined || value === null || value === '') return null;

    const number = Number(String(value).replace('%', '').replace(',', '').trim());
    return Number.isNaN(number) ? null : number;
}

async function buildCompanyMetricRankings(currentSlug) {
    const companiesIndex = await fetchJson('companies.json', []);
    if (!Array.isArray(companiesIndex) || !companiesIndex.length) return null;

    const companyDetails = await Promise.all(
        companiesIndex.map(async item => {
            if (!item.slug) return item;
            const detail = await fetchJson(`companies/${item.slug}.json`, item);
            return { ...item, ...detail };
        })
    );

    function rankMetric(fieldName, higherIsBetter = true) {
        const eligible = companyDetails
            .map(company => ({
                slug: company.slug,
                name: company.companyName || company.name || company.slug,
                value: getNumericField(company, fieldName)
            }))
            .filter(item => item.value !== null);

        eligible.sort((a, b) => higherIsBetter ? b.value - a.value : a.value - b.value);

        const rank = eligible.findIndex(item => item.slug === currentSlug) + 1;

        if (!rank || eligible.length < 2) return null;

        return { rank, total: eligible.length };
    }

    return {
        peRatio: rankMetric('peRatio', false),
        dividendYield: rankMetric('dividendYield', true)
    };
}

function renderMetricRankings(rankings) {
    if (!rankings) return;

    if (rankings.peRatio) {
        setText('pe-ratio-rank', `#${rankings.peRatio.rank} of ${rankings.peRatio.total} by lowest P/E`);
    } else {
        setText('pe-ratio-rank', 'Ranking available once peer data exists');
    }

    if (rankings.dividendYield) {
        setText('dividend-yield-rank', `#${rankings.dividendYield.rank} of ${rankings.dividendYield.total} by highest yield`);
    } else {
        setText('dividend-yield-rank', 'Ranking available once peer data exists');
    }
}


function renderCompanyLogo(company, stockSlug) {
    const companyLogo = document.getElementById('company-logo');
    if (!companyLogo) return;

    const logoUrl = getLogoUrl(company, 256);
    const fallbackText = companyInitials(company, stockSlug);

    companyLogo.onerror = () => {
        companyLogo.style.display = 'none';

        const logoPanel = companyLogo.parentElement;
        if (!logoPanel || logoPanel.querySelector('.company-logo-placeholder.hero-logo-placeholder')) return;

        const fallback = document.createElement('div');
        fallback.className = 'company-logo-placeholder hero-logo-placeholder';
        fallback.textContent = fallbackText;
        logoPanel.appendChild(fallback);
    };

    if (logoUrl) {
        companyLogo.crossOrigin = 'anonymous';
        companyLogo.referrerPolicy = 'no-referrer';
        companyLogo.src = logoUrl;
        companyLogo.alt = `${company.companyName || stockSlug} logo`;
        companyLogo.style.display = 'block';
    } else {
        companyLogo.onerror();
    }
}


function initSlvrHelp() {
    const button = document.querySelector('.slvr-help');
    const explainer = document.getElementById('slvr-explainer');

    if (!button || !explainer) return;

    button.addEventListener('click', () => {
        const isHidden = explainer.hasAttribute('hidden');

        if (isHidden) {
            explainer.removeAttribute('hidden');
            button.setAttribute('aria-expanded', 'true');
        } else {
            explainer.setAttribute('hidden', '');
            button.setAttribute('aria-expanded', 'false');
        }
    });
}

function initTabs() {
    const buttons = document.querySelectorAll('.tab-button');
    const panels = document.querySelectorAll('.tab-panel');

    buttons.forEach(button => {
        button.addEventListener('click', () => {
            const target = button.dataset.tab;

            buttons.forEach(item => item.classList.remove('active'));
            panels.forEach(panel => panel.classList.remove('active'));

            button.classList.add('active');

            const panel = document.getElementById(`tab-${target}`);
            if (panel) panel.classList.add('active');
        });
    });
}

function initAcquisitionSort() {
    const sortSelect = document.getElementById('acquisition-sort');
    if (!sortSelect) return;

    sortSelect.addEventListener('change', () => {
        acquisitionState.sortMode = sortSelect.value;
        renderAcquisitions(
            acquisitionState.acquisitions,
            acquisitionState.history,
            acquisitionState.company
        );
    });
}

function initTimelineControls() {
    const carousel = document.getElementById('timeline-carousel');
    const prev = document.getElementById('timeline-prev');
    const next = document.getElementById('timeline-next');

    if (!carousel || !prev || !next) return;

    prev.addEventListener('click', () => {
        carousel.scrollBy({ left: -360, behavior: 'smooth' });
    });

    next.addEventListener('click', () => {
        carousel.scrollBy({ left: 360, behavior: 'smooth' });
    });
}

function strategicEvidenceDate(initiative) {
    const dates = (initiative.evidence || [])
        .map(item => parseDate(item.date))
        .filter(Boolean)
        .map(item => item.getTime());
    return dates.length ? Math.max(...dates) : 0;
}

function strategicInitiativesForDisplay() {
    const initiatives = Array.isArray(strategicIntelligenceState.data?.initiatives)
        ? [...strategicIntelligenceState.data.initiatives]
        : [];
    const filtered = strategicIntelligenceState.category === 'All'
        ? initiatives
        : initiatives.filter(item => item.category === strategicIntelligenceState.category);

    return filtered.sort((a, b) => {
        if (strategicIntelligenceState.sortMode === 'recent') {
            return strategicEvidenceDate(b) - strategicEvidenceDate(a) || String(a.name || '').localeCompare(String(b.name || ''));
        }
        if (strategicIntelligenceState.sortMode === 'category') {
            return String(a.category || 'Other').localeCompare(String(b.category || 'Other')) || String(a.name || '').localeCompare(String(b.name || ''));
        }
        const relevance = Number(b.relevancyScore) - Number(a.relevancyScore);
        return (Number.isFinite(relevance) ? relevance : 0) || strategicEvidenceDate(b) - strategicEvidenceDate(a) || String(a.name || '').localeCompare(String(b.name || ''));
    });
}

function renderStrategicEvidence(evidence) {
    if (!Array.isArray(evidence) || !evidence.length) return '';
    const items = evidence.map(item => {
        const sourceUrl = safeExternalUrl(item.sourceUrl);
        const date = item.date ? formatDate(item.date) : (item.fiscalYear ? `FY ${escapeHtml(item.fiscalYear)}` : 'Date not supplied');
        const source = item.sourceDocument ? `<span>${escapeHtml(item.sourceDocument)}</span>` : '';
        const page = item.pageNumber ? `<span>Page ${escapeHtml(item.pageNumber)}</span>` : '';
        const sourceLink = sourceUrl ? `<a href="${escapeHtml(sourceUrl)}" target="_blank" rel="noopener noreferrer">Open original source<span class="visually-hidden"> in a new tab</span></a>` : '';
        return `
            <li class="strategic-evidence-item">
                <h4>${escapeHtml(item.title || 'Supporting evidence')}</h4>
                ${item.summary ? `<p>${escapeHtml(item.summary)}</p>` : ''}
                ${item.quote ? `<div class="strategic-quote-label">Quoted source text</div><blockquote>${escapeHtml(item.quote)}</blockquote>` : ''}
                <div class="strategic-evidence-meta"><span>${date}</span>${source}${page}${sourceLink}</div>
            </li>`;
    }).join('');
    return `<details class="strategic-evidence"><summary>View supporting sources (${evidence.length})</summary><ul>${items}</ul></details>`;
}

function renderStrategicIntelligence() {
    const container = document.getElementById('strategic-intelligence-content');
    if (!container) return;

    const data = strategicIntelligenceState.data;
    const metadata = strategicIntelligenceState.metadata || {};
    const initiatives = Array.isArray(data?.initiatives) ? data.initiatives : [];
    if (!data || data.status !== 'available' || !initiatives.length) {
        container.innerHTML = `
            <div class="strategic-empty-state">
                <p>Strategic intelligence is not currently available for this company.</p>
                <p class="muted">SignalStrata data will appear here after it is available and synchronised.</p>
            </div>
            ${renderStrategicAttribution(metadata, null)}`;
        return;
    }

    const categories = ['All', ...new Set(initiatives.map(item => item.category || 'Other'))];
    const displayed = strategicInitiativesForDisplay();
    const latestEvidence = data.latestEvidenceDate || metadata.latestEvidenceDate;
    const failedTickers = Array.isArray(metadata.failedTickers) ? metadata.failedTickers : [];
    const isStale = failedTickers.includes(data.ticker);

    container.innerHTML = `
        <div class="strategic-overview" aria-label="Strategic intelligence overview">
            <div><strong>${initiatives.length}</strong><span>identified initiative${initiatives.length === 1 ? '' : 's'}</span></div>
            <div><strong>${latestEvidence ? formatDate(latestEvidence) : 'Not supplied'}</strong><span>latest evidence</span></div>
            <div><strong>${categories.length - 1}</strong><span>activity area${categories.length === 2 ? '' : 's'}</span></div>
        </div>
        ${isStale ? '<p class="strategic-stale-note">Showing the last successful synchronisation while a provider refresh is retried.</p>' : ''}
        <div class="strategic-controls">
            <div class="strategic-filters" aria-label="Filter initiatives by category">
                ${categories.map(category => `<button class="strategic-filter${strategicIntelligenceState.category === category ? ' active' : ''}" type="button" data-strategic-category="${escapeHtml(category)}">${escapeHtml(category)}</button>`).join('')}
            </div>
            <label class="sort-control strategic-sort-control"><span>Sort by</span><select id="strategic-sort"><option value="useful">Relevance and recency</option><option value="recent">Most recent evidence</option><option value="category">Category</option></select></label>
        </div>
        <div class="strategic-initiative-list">
            ${displayed.length ? displayed.map(initiative => renderStrategicInitiative(initiative)).join('') : '<p class="muted">No initiatives match this category.</p>'}
        </div>
        ${renderStrategicAttribution(metadata, data)}`;

    const sort = document.getElementById('strategic-sort');
    if (sort) {
        sort.value = strategicIntelligenceState.sortMode;
        sort.addEventListener('change', () => {
            strategicIntelligenceState.sortMode = sort.value;
            renderStrategicIntelligence();
        });
    }
    container.querySelectorAll('[data-strategic-category]').forEach(button => {
        button.addEventListener('click', () => {
            strategicIntelligenceState.category = button.dataset.strategicCategory || 'All';
            renderStrategicIntelligence();
        });
    });
}

function renderStrategicInitiative(initiative) {
    const evidence = Array.isArray(initiative.evidence) ? initiative.evidence : [];
    const detailRows = [
        initiative.activityStage ? `<span><b>Stage</b> ${escapeHtml(initiative.activityStage)}</span>` : '',
        initiative.activityType ? `<span><b>Type</b> ${escapeHtml(initiative.activityType)}</span>` : '',
        initiative.investmentSummary ? `<span><b>Investment</b> ${escapeHtml(initiative.investmentSummary)}</span>` : ''
    ].filter(Boolean).join('');
    const related = ['partners', 'products', 'platforms', 'entities'].flatMap(key => Array.isArray(initiative[key]) ? initiative[key] : []).filter(Boolean);
    return `
        <article class="strategic-initiative">
            <div class="strategic-initiative-heading"><div><span class="strategic-category">${escapeHtml(initiative.category || 'Other')}</span><h3>${escapeHtml(initiative.name || 'Untitled initiative')}</h3></div><span class="strategic-evidence-count">${evidence.length} source${evidence.length === 1 ? '' : 's'}</span></div>
            ${initiative.summary ? `<p>${escapeHtml(initiative.summary)}</p>` : '<p class="muted">No provider summary is available for this initiative.</p>'}
            ${detailRows ? `<div class="strategic-initiative-meta">${detailRows}</div>` : ''}
            ${related.length ? `<p class="strategic-related"><b>Related:</b> ${escapeHtml([...new Set(related)].join(', '))}</p>` : ''}
            ${renderStrategicEvidence(evidence)}
        </article>`;
}

function renderStrategicAttribution(metadata, companyData) {
    const synchronised = metadata.lastSuccessfulSynchronisation;
    const latest = companyData?.latestEvidenceDate || metadata.latestEvidenceDate;
    const metadataText = synchronised ? `Last synchronised: ${formatDate(synchronised)}` : 'Not yet synchronised';
    const latestText = companyData && latest ? `Latest evidence: ${formatDate(latest)}` : '';
    return `<div class="strategic-attribution"><span>Data powered by <a class="signalstrata-logo-link" href="https://signalstrata.io/" target="_blank" rel="noopener noreferrer"><img src="images/signalstrata-logo.png" alt="SignalStrata"><span class="visually-hidden"> (opens in a new tab)</span></a></span><span>${metadataText}${latestText ? ` · ${latestText}` : ''}</span></div>`;
}

async function loadCompany() {
    if (!stock) {
        document.getElementById('company').innerHTML = '<h2>No company specified</h2>';
        return;
    }

    try {
        const [companyDetail, ai, risk, leadership, acquisitions, events, historyData, companiesIndex, rankings, strategicData, strategicMetadata] = await Promise.all([
            fetchJson(`companies/${stock}.json`),
            fetchJson(`ai/${stock}-summary.json`, {}),
            fetchJson(`risk/${stock}-risk.json`, {}),
            fetchJson(`leadership/${stock}-ceo.json`, {}),
            fetchJson(`acquisitions/${stock}-acquisitions.json`, {}),
            fetchJson(`events/${stock}-events.json`, {}),
            fetchJson(`history/${stock}-history.json`, {}),
            fetchJson('companies.json', []),
            buildCompanyMetricRankings(stock),
            fetchJson(`strategic-intelligence/${stock}.json`, {}),
            fetchJson('strategic-intelligence/metadata.json', {})
        ]);

        const indexCompany = Array.isArray(companiesIndex)
            ? companiesIndex.find(item => item.slug === stock || item.ticker === stock)
            : null;
        const company = { ...(indexCompany || {}), ...(companyDetail || {}) };

        const history = normaliseHistory(historyData);
        const vulnerabilityMarkers = risk.vulnerabilityMarkers || [];

        document.title = `${company.companyName || stock} | StockLayer`;

        renderCompanyLogo(company, stock);

        setText('company-kicker', `${company.sector || ''} • ${company.industry || ''}`);

        setText(
            'company-name',
            `${company.companyName || stock} ${company.ticker ? `(${company.ticker})` : ''}`
        );

        setText('company-summary', company.summary || ai.headline || 'No summary available.');

        renderPriceInsight(company, history);

        setText('market-cap', formatMarketCap(company.marketCap));

        setText(
            'market-cap-rank',
            company.marketCapRank ? `#${company.marketCapRank} globally by market cap` : '-'
        );

        setText('pe-ratio', company.peRatio || '-');
        setText('dividend-yield', company.dividendYield ? `${company.dividendYield}%` : '-');
        renderMetricRankings(rankings);

        const volatility = risk.stockLayerVolatilityRating || {};
        const volatilityText = volatility.score
            ? `${volatility.score}/10`
            : (company.volatilityRating ? `${company.volatilityRating}/10` : '-');
        const volatilityLabel = volatility.label || company.volatilityLabel || '-';

        setText('volatility-rating', volatilityText);
        setText('volatility-label', volatilityLabel);

        const aiMarker = findRiskMarker(vulnerabilityMarkers, 'AI');
        const marketMarker = findRiskMarker(vulnerabilityMarkers, 'financial');
        const acquisitionMarker = findRiskMarker(vulnerabilityMarkers, 'acquisition');

        setText('lens-volatility', `SLVR ${volatilityText}`);
        setText('lens-summary', ai.stockLayerView || ai.headline || company.summary || 'No StockLayer view available.');
        setText('lens-pill-volatility', volatilityLabel);
        setText('lens-pill-ai', aiMarker ? `${aiMarker.label} (${aiMarker.score}/10)` : '-');
        setText('lens-pill-market', marketMarker ? `${marketMarker.label} (${marketMarker.score}/10)` : '-');
        setText('lens-pill-acquisition', acquisitionMarker ? `${acquisitionMarker.label} (${acquisitionMarker.score}/10)` : '-');

        const overviewSoWhat = ai.stockLayerView
            ? ai.stockLayerView
            : 'The key question is whether the company can turn its current strengths into sustained shareholder returns.';

        setText('overview-so-what', overviewSoWhat);

        renderInvestmentTool(history, company);

        setText(
            'stocklayer-view',
            ai.stockLayerView || ai.headline || company.summary || 'No StockLayer view available.'
        );

        renderList('why-investors-own-it', ai.whyInvestorsOwnIt || []);
        renderList('bull-case', ai.bullCase || company.bullCase || []);
        renderList('bear-case', ai.bearCase || company.bearCase || []);

        renderVulnerabilityMarkers(vulnerabilityMarkers);
        renderCeoWatch(leadership, history, company);

        acquisitionState = {
            acquisitions,
            history,
            company,
            sortMode: acquisitionState.sortMode
        };

        renderAcquisitions(acquisitions, history, company);
        renderTimeline(events.events || [], acquisitions.majorAcquisitions || []);
        strategicIntelligenceState = {
            data: strategicData,
            metadata: strategicMetadata,
            category: 'All',
            sortMode: 'useful'
        };
        renderStrategicIntelligence();

    } catch (error) {
        console.error(error);
        document.getElementById('company').innerHTML = `
            <section class="content-card error-card">
                <h2>Unable to load ${stock}</h2>
                <p>${error.message}</p>
                <p>Check the GitHub JSON files exist for this company.</p>
            </section>
        `;
    }
}

function renderVulnerabilityMarkers(markers) {
    const el = document.getElementById('vulnerability-markers');
    el.innerHTML = '';

    if (!markers.length) {
        el.innerHTML = '<p>No vulnerability markers available yet.</p>';
        return;
    }

    markers.forEach(marker => {
        const item = document.createElement('div');
        item.className = 'risk-item';

        item.innerHTML = `
            <div class="risk-topline">
                <strong>${marker.category || 'Risk'}</strong>
                <span>${marker.label || ''} ${marker.score ? `• ${marker.score}/10` : ''}</span>
            </div>
            ${scoreBar(marker.score || 0)}
            <p>${marker.explanation || ''}</p>
        `;

        el.appendChild(item);
    });
}

function renderCeoWatch(leadership, history, company) {
    const el = document.getElementById('ceo-watch');

    if (!leadership || !leadership.ceoName) {
        el.innerHTML = '<p>No CEO data available yet.</p>';
        return;
    }

    const performance = calculateCeoSharePricePerformance(leadership, history, company);

    el.innerHTML = `
        <div class="ceo-card">
            <h3>${leadership.ceoName}</h3>
            <p class="muted">${leadership.role || 'Chief Executive Officer'}</p>

            <div class="mini-grid">
                <div><span>Age</span><strong>${leadership.age || '-'}</strong></div>
                <div><span>CEO Since</span><strong>${formatDate(leadership.ceoSince) || '-'}</strong></div>
                <div><span>Tenure</span><strong>${leadership.tenureYears ? `${leadership.tenureYears} years` : '-'}</strong></div>
                <div><span>CEO Score</span><strong>${leadership.ceoWatchScore ? `${leadership.ceoWatchScore}/10` : '-'}</strong></div>
            </div>

            <div class="ceo-performance">
                <h4>Share price during tenure</h4>
                ${
                    performance
                        ? `
                            <div class="ceo-performance-grid">
                                <div><span>Start price</span><strong>${formatCurrency(performance.startPrice, company.currency)}</strong></div>
                                <div><span>Current price</span><strong>${formatCurrency(performance.currentPrice, company.currency)}</strong></div>
                                <div><span>Improvement</span><strong class="${performance.improvement >= 0 ? 'positive' : 'negative'}">${formatPercent(performance.improvement)}</strong></div>
                                <div><span>From</span><strong>${formatDate(performance.startDate)}</strong></div>
                            </div>
                        `
                        : `
                            <p class="muted">
                                Add sharePriceAtStart to the CEO JSON or provide sufficient
                                price history to calculate this.
                            </p>
                        `
                }
            </div>

            <p>${leadership.impactSummary || ''}</p>

            <h4>Watch Points</h4>
            <ul>${(leadership.watchPoints || []).map(point => `<li>${point}</li>`).join('')}</ul>
        </div>
    `;
}

function getAcquisitionLogoUrl(acquisition) {
    const domains = {
        "splunk": "splunk.com",
        "webex": "webex.com",
        "tandberg": "cisco.com",
        "meraki": "meraki.cisco.com",
        "opendns": "opendns.com",
        "appdynamics": "appdynamics.com",
        "duo security": "duo.com",
        "duo": "duo.com",
        "acacia communications": "acacia-inc.com",
        "acacia": "acacia-inc.com",
        "thousandeyes": "thousandeyes.com",
        "jasper": "cisco.com",
        "sourcefire": "cisco.com",
        "scientific atlanta": "cisco.com",
        "cerent": "cisco.com",
        "stratacom": "cisco.com",
        "isovalent": "isovalent.com",
        "galileo technologies": "rungalileo.io",
        "galileo": "rungalileo.io"
    };

    const explicitDomain = acquisition.domain || acquisition.logoDomain;
    const key = String(acquisition.name || '').toLowerCase();
    const domain = explicitDomain || domains[key];

    if (!domain) return '';

    return `https://img.logo.dev/${domain}?token=${LOGO_DEV_TOKEN}&size=64`;
}

function getAcquisitionLogoHtml(acquisition) {
    const logoUrl = getAcquisitionLogoUrl(acquisition);
    const initials = String(acquisition.name || '?')
        .split(/\s+/)
        .filter(Boolean)
        .slice(0, 2)
        .map(part => part[0])
        .join('')
        .toUpperCase();

    if (!logoUrl) {
        return `<div class="acquisition-logo-placeholder">${initials || '?'}</div>`;
    }

    return `<img class="acquisition-logo" src="${logoUrl}" alt="${acquisition.name || 'Acquisition'} logo" crossorigin="anonymous" referrerpolicy="no-referrer" onerror="this.outerHTML='<div class=&quot;acquisition-logo-placeholder&quot;>${initials || '?'}</div>'">`;
}

function isAcquisitionTooEarlyToJudge(acquisition) {
    const status = String(acquisition.status || '').toLowerCase();
    const name = String(acquisition.name || '').toLowerCase();
    const acquisitionDate = parseDate(acquisition.acquisitionDate);
    const now = new Date();

    if (status.includes('pending') || status.includes('announced') || name.includes('galileo')) {
        return true;
    }

    if (!acquisitionDate) return false;

    const monthsSinceAcquisition = (now.getFullYear() - acquisitionDate.getFullYear()) * 12 + (now.getMonth() - acquisitionDate.getMonth());

    return monthsSinceAcquisition < 18;
}

function getAcquisitionClassification(acquisition) {
    if (isAcquisitionTooEarlyToJudge(acquisition)) return 'Too early to judge';
    if (acquisition.classification) return acquisition.classification;

    const status = String(acquisition.status || '').toLowerCase();
    const score = Number(acquisition.stockLayerImpactScore || 0);

    if (status.includes('transformational') || score >= 9) return 'Transformational';
    if (status.includes('successful') || score >= 8) return 'Successful';
    if (status.includes('strategic') || score >= 7) return 'Strategic';
    if (status.includes('mixed') || score >= 5) return 'Mixed';
    if (score > 0) return 'Questionable';

    return 'Unclassified';
}

function getClassificationClass(classification) {
    return String(classification || 'Unclassified')
        .toLowerCase()
        .replace(/[^a-z0-9]+/g, '-');
}

function buildInvestorTakeaway(items) {
    if (!items || !items.length) {
        return 'No acquisition data is available yet.';
    }

    const softwareSecurityDeals = items.filter(item => {
        const text = `${item.category || ''} ${item.summary || ''}`.toLowerCase();
        return text.includes('software') || text.includes('security') || text.includes('observability') || text.includes('cloud');
    }).length;

    const highScoringDeals = items.filter(item => Number(item.stockLayerImpactScore || 0) >= 8).length;
    const mixedDeals = items.filter(item => Number(item.stockLayerImpactScore || 0) > 0 && Number(item.stockLayerImpactScore || 0) <= 6).length;

    const softwareSecurityShare = Math.round((softwareSecurityDeals / items.length) * 100);
    const highScoringShare = Math.round((highScoringDeals / items.length) * 100);

    let takeaway = `Cisco appears to be an acquisition-led company with a generally strong record. ${highScoringShare}% of the reviewed deals score 8/10 or above on StockLayer's perceived effectiveness scale.`;

    if (softwareSecurityShare >= 50) {
        takeaway += ` Its strongest pattern is software, security, cloud and observability-led acquisitions, which account for roughly ${softwareSecurityShare}% of the reviewed deals.`;
    }

    if (mixedDeals > 0) {
        takeaway += ` The weaker or more mixed deals tend to be where Cisco moved into markets that later became less central to its long-term growth story.`;
    }

    takeaway += ' Future acquisitions should be judged by whether they deepen recurring revenue, security, observability, AI infrastructure or cloud networking rather than simply adding scale.';

    return takeaway;
}

function renderAcquisitionReturns(acquisition, history, company) {
    if (isAcquisitionTooEarlyToJudge(acquisition)) {
        return `
            <div class="acquisition-returns unavailable">
                <span>Share price response</span>
                <strong>Too early to assess</strong>
            </div>
        `;
    }

    const returns = calculateAcquisitionReturns(acquisition, history, company);

    if (!returns) {
        return `
            <div class="acquisition-returns unavailable">
                <span>Share price response</span>
                <strong>Needs closer historical data</strong>
            </div>
        `;
    }

    const cells = returns.results.map(result => {
        if (result.value === null || result.value === undefined || Number.isNaN(result.value)) {
            return `
                <div>
                    <span>${result.label}</span>
                    <strong>-</strong>
                </div>
            `;
        }

        const positive = result.value >= 0;

        return `
            <div>
                <span>${result.label}</span>
                <strong class="${positive ? 'positive' : 'negative'}">${formatPercent(result.value)}</strong>
            </div>
        `;
    }).join('');

    return `
        <div class="acquisition-returns">
            <div class="acquisition-returns-title">
                Share price response since ${formatDate(returns.startDate)}
                <span>Start price ${formatCurrency(returns.startPrice, company.currency)}</span>
            </div>
            <div class="acquisition-return-grid">
                ${cells}
            </div>
        </div>
    `;
}

function getSortedAcquisitions(items) {
    const sortMode = acquisitionState.sortMode || 'effectiveness-desc';

    return [...items].sort((a, b) => {
        const dateA = parseDate(a.acquisitionDate);
        const dateB = parseDate(b.acquisitionDate);
        const timeA = dateA ? dateA.getTime() : 0;
        const timeB = dateB ? dateB.getTime() : 0;

        const costA = parseCostToBillions(a.value);
        const costB = parseCostToBillions(b.value);
        const safeCostA = costA === null ? -1 : costA;
        const safeCostB = costB === null ? -1 : costB;

        const scoreA = Number(a.stockLayerImpactScore || 0);
        const scoreB = Number(b.stockLayerImpactScore || 0);

        if (sortMode === 'date-desc') return timeB - timeA;
        if (sortMode === 'date-asc') return timeA - timeB;
        if (sortMode === 'cost-desc') return safeCostB - safeCostA;
        if (sortMode === 'cost-asc') return safeCostA - safeCostB;
        if (sortMode === 'effectiveness-asc') return scoreA - scoreB;

        return scoreB - scoreA;
    });
}

function renderAcquisitionScorecard(items) {
    const el = document.getElementById('acquisition-scorecard');
    if (!el) return;

    if (!items || !items.length) {
        el.innerHTML = '';
        return;
    }

    const knownCosts = items
        .map(item => parseCostToBillions(item.value))
        .filter(value => value !== null);

    const totalKnownCost = knownCosts.reduce((sum, value) => sum + value, 0);
    const scoredItems = items.filter(item => Number(item.stockLayerImpactScore || 0) > 0 && !isAcquisitionTooEarlyToJudge(item));
    const averageScore = scoredItems.length
        ? scoredItems.reduce((sum, item) => sum + Number(item.stockLayerImpactScore || 0), 0) / scoredItems.length
        : 0;

    const highScoringDeals = scoredItems.filter(item => Number(item.stockLayerImpactScore || 0) >= 8).length;
    const successRate = scoredItems.length ? Math.round((highScoringDeals / scoredItems.length) * 100) : 0;

    const topScore = [...items].sort((a, b) => Number(b.stockLayerImpactScore || 0) - Number(a.stockLayerImpactScore || 0))[0];
    const largestKnown = [...items]
        .filter(item => parseCostToBillions(item.value) !== null)
        .sort((a, b) => parseCostToBillions(b.value) - parseCostToBillions(a.value))[0];

    const transformational = items.find(item => getAcquisitionClassification(item) === 'Transformational') || topScore;

    el.innerHTML = `
        <div class="scorecard-tile">
            <span>Deals reviewed</span>
            <strong>${items.length}</strong>
        </div>
        <div class="scorecard-tile">
            <span>Success rate</span>
            <strong>${successRate}%</strong>
        </div>
        <div class="scorecard-tile">
            <span>Average score</span>
            <strong>${averageScore.toFixed(1)}/10</strong>
        </div>
        <div class="scorecard-tile">
            <span>Known spend</span>
            <strong>$${totalKnownCost.toFixed(1)}bn</strong>
        </div>
        <div class="scorecard-tile wide">
            <span>Largest deal</span>
            <strong>${largestKnown ? largestKnown.name : '-'}</strong>
        </div>
        <div class="scorecard-tile wide">
            <span>Most transformational</span>
            <strong>${transformational ? transformational.name : '-'}</strong>
        </div>
    `;
}

function renderAcquisitionPortfolio(items) {
    const el = document.getElementById('acquisition-portfolio');
    if (!el) return;

    if (!items || !items.length) {
        el.innerHTML = '<p>No acquisition data available yet.</p>';
        return;
    }

    const groupOrder = ['Transformational', 'Successful', 'Strategic', 'Mixed', 'Questionable', 'Too early to judge', 'Unclassified'];
    const groups = {};

    items.forEach(item => {
        const classification = getAcquisitionClassification(item);
        if (!groups[classification]) groups[classification] = [];
        groups[classification].push(item);
    });

    el.innerHTML = groupOrder
        .filter(group => groups[group] && groups[group].length)
        .map(group => {
            const itemsHtml = groups[group]
                .sort((a, b) => Number(b.stockLayerImpactScore || 0) - Number(a.stockLayerImpactScore || 0))
                .map(item => `
                    <div class="portfolio-chip">
                        <strong>${item.name}</strong>
                        <span>${item.stockLayerImpactScore || '-'}/10</span>
                    </div>
                `)
                .join('');

            return `
                <div class="portfolio-group portfolio-${getClassificationClass(group)}">
                    <h3>${group}</h3>
                    <div class="portfolio-chip-list">${itemsHtml}</div>
                </div>
            `;
        })
        .join('');
}

function renderAcquisitions(acquisitions, history, company) {
    setText('acquisition-summary', acquisitions.acquisitionStrategySummary || 'No acquisition summary available.');

    const items = acquisitions.majorAcquisitions || [];

    renderAcquisitionScorecard(items);
    renderAcquisitionPortfolio(items);
    setText('acquisition-investor-takeaway', acquisitions.investorTakeaway || buildInvestorTakeaway(items));

    const el = document.getElementById('major-acquisitions');
    el.innerHTML = '';

    if (!items.length) {
        el.innerHTML = '<p>No acquisition data available yet.</p>';
        return;
    }

    const sortedItems = getSortedAcquisitions(items);

    sortedItems.forEach((acquisition, index) => {
        const card = document.createElement('details');
        card.className = 'acquisition-card refined-acquisition-card';
        const costInBillions = parseCostToBillions(acquisition.value);
        const costLabel = costInBillions === null ? 'Cost undisclosed' : `$${costInBillions.toFixed(costInBillions >= 10 ? 1 : 2)}bn`;
        const classification = getAcquisitionClassification(acquisition);

        card.innerHTML = `
            <summary>
                <div class="acquisition-summary-main">
                    <div class="acquisition-title-row">
                        ${getAcquisitionLogoHtml(acquisition)}
                        <div>
                            <h3>${acquisition.name || 'Acquisition'}</h3>
                            <p class="muted">
                                ${[acquisition.year, acquisition.category, acquisition.acquisitionDate ? formatDate(acquisition.acquisitionDate) : ''].filter(Boolean).join(' | ')}
                            </p>
                        </div>
                    </div>
                    <div class="acquisition-outcome">
                        <span class="classification-pill classification-${getClassificationClass(classification)}">${classification}</span>
                        <strong>${isAcquisitionTooEarlyToJudge(acquisition) ? 'TBC' : `${acquisition.stockLayerImpactScore || '-'}/10`}</strong>
                        <span class="details-toggle" aria-hidden="true"></span>
                    </div>
                </div>
            </summary>

            <div class="acquisition-detail-body">
                <div class="acquisition-meta-grid">
                    <div><span>Value</span><strong>${acquisition.value || '-'}</strong></div>
                    <div><span>Parsed cost</span><strong>${costLabel}</strong></div>
                    <div><span>Status</span><strong>${acquisition.status || classification}</strong></div>
                    <div><span>Effectiveness</span><strong>${isAcquisitionTooEarlyToJudge(acquisition) ? 'Too early to judge' : `${acquisition.stockLayerImpactScore || '-'}/10`}</strong></div>
                </div>

                <div class="acquisition-two-col">
                    <div>
                        <h4>Why Cisco bought it</h4>
                        <p>${acquisition.whyPurchased || acquisition.summary || 'No rationale available yet.'}</p>
                    </div>
                    <div>
                        <h4>Investor takeaway</h4>
                        <p>${isAcquisitionTooEarlyToJudge(acquisition) ? 'This deal is too recent to judge properly. StockLayer will treat it as a watchlist item until there is evidence of integration progress, product impact, revenue contribution or strategic value.' : (acquisition.investorTakeaway || acquisition.impactQuestion || 'No investor takeaway available yet.')}</p>
                    </div>
                </div>

                ${renderAcquisitionReturns(acquisition, history, company)}
            </div>
        `;

        el.appendChild(card);
    });
}

function buildTimelineItems(events, acquisitions) {
    const eventItems = (events || []).map(event => ({
        date: event.date,
        parsedDate: parseDate(event.date),
        type: event.type || 'event',
        title: event.title || 'Event',
        summary: event.summary || '',
        lesson: event.stockLayerLesson || '',
        badge: 'Event'
    }));

    const acquisitionItems = (acquisitions || []).map(acquisition => ({
        date: acquisition.acquisitionDate,
        parsedDate: parseDate(acquisition.acquisitionDate),
        type: 'acquisition',
        title: acquisition.name || 'Acquisition',
        summary: `${acquisition.category || 'Acquisition'} | ${acquisition.value || 'Value unavailable'} | Effectiveness ${acquisition.stockLayerImpactScore || '-'}/10`,
        lesson: acquisition.impactQuestion || acquisition.summary || '',
        badge: 'Acquisition'
    }));

    return [...eventItems, ...acquisitionItems]
        .filter(item => item.parsedDate)
        .sort((a, b) => a.parsedDate - b.parsedDate);
}

function renderTimeline(events, acquisitions) {
    const el = document.getElementById('timeline-carousel');
    if (!el) return;

    const items = buildTimelineItems(events, acquisitions);

    el.innerHTML = '';

    if (!items.length) {
        el.innerHTML = '<p>No event timeline available yet.</p>';
        return;
    }

    items.forEach(item => {
        const card = document.createElement('article');
        card.className = `timeline-card ${item.type === 'acquisition' ? 'timeline-acquisition' : ''}`;

        card.innerHTML = `
            <div class="timeline-card-date">${formatDate(item.date)}</div>
            <div class="timeline-card-badge">${item.badge}</div>
            <h3>${item.title}</h3>
            <p>${item.summary}</p>
            ${item.lesson ? `<div class="timeline-card-lesson">${item.lesson}</div>` : ''}
        `;

        el.appendChild(card);
    });
}

initTabs();
initSlvrHelp();
initAcquisitionSort();
initTimelineControls();
loadCompany();

const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const path = require('node:path');
const root = path.resolve(__dirname, '..');
const html = fs.readFileSync(path.join(root, 'index.html'), 'utf8');
const manifest = JSON.parse(fs.readFileSync(path.join(root, 'ftse100-membership.json'), 'utf8'));
const section = (start, end) => html.slice(html.indexOf(start), html.indexOf(end, html.indexOf(start)));
const code = section('    let ftse100Tickers =', '    const COMPANY_USPS =')
  + section('    async function refreshLivePrices(', '    function startLivePriceRefresh()');
const companies = manifest.members.map(c => ({ ...c, currentPrice: 10, priceDate: new Date().toISOString() }));
const context = vm.createContext({
  Date, console, DATA_BASE_URL: 'https://example.test/', state: { companies: [] },
  fetch: async () => ({ ok: true, json: async () => manifest }),
  fetchCompanyDataset: async () => ({ companies, source: { baseUrl: 'https://example.test/' } }),
  applyMarketCapRanks: c => c, loadGlobalRankHistory: async () => {}, renderCompanies: () => {}
});
vm.runInContext(code, context);
async function run() {
  context.input = companies;
  assert.equal(vm.runInContext('normaliseCompanyDataset(input).length', context), 100);
  context.input = companies.filter(c => c.ticker !== 'WPP.L');
  assert.throws(() => vm.runInContext('normaliseCompanyDataset(input)', context), /99 of the 100/);
  context.input = [...context.input, { slug: 'beazley', ticker: 'BEZ.L' }];
  assert.throws(() => vm.runInContext('normaliseCompanyDataset(input)', context), /99 of the 100/);
  context.input = [...companies.slice(0, 99), companies[0]];
  assert.throws(() => vm.runInContext('normaliseCompanyDataset(input)', context), /99 of the 100/);
  context.state.companies = [{ slug: 'beazley', ticker: 'BEZ.L' }, { slug: 'schroders', ticker: 'SDR.L' }];
  await vm.runInContext('refreshLivePrices()', context);
  assert.equal(context.state.companies.length, 100);
  assert(context.state.companies.some(c => c.ticker === 'WPP.L'));
  assert(context.state.companies.some(c => c.ticker === 'EZJ.L'));
  assert(!context.state.companies.some(c => c.ticker === 'BEZ.L'));
  console.log('Passed 5 browser membership checks, including membership changes during refresh');
}
run().catch(error => { console.error(error); process.exitCode = 1; });

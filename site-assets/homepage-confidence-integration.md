# Homepage confidence integration

The tested homepage reads `growthConfidence`, `growthConfidenceLabel`, and `evidenceCoverage` from `company-reports/manifest.json` through `StockLayerCompanyReport.getSummary(slug)`.

In `getGrowthPotentialScore(company)`, use the manifest value before any legacy fallback:

```js
const measured = window.StockLayerCompanyReport?.getSummary?.(company.slug);
const measuredScore = Number(measured?.growthConfidence);
if (Number.isFinite(measuredScore)) {
  return {
    score: clampScore(measuredScore),
    placeholder: false,
    source: 'company-report-signals',
    rating: measured.growthConfidenceLabel,
    evidenceCoverage: Number(measured.evidenceCoverage),
    components: null
  };
}
```

Listen for `stocklayer:company-reports-ready` and rerender the company cards. Display the value as `{score}/100`, with the label and `analysis signals` beneath it. The local tested integration is in `index2.html` in the StockLayer website workspace.

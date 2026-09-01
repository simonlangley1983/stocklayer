# Customer intelligence report assets

These are the versioned frontend assets for the four-section company report opened from the StockLayer company list.

The hosted page must include:

```html
<link rel="stylesheet" href="customer-report.css">
<script src="customer-report.js" defer></script>
```

At the start of the existing `openMethodologyModal(company)` function, after its modal elements have been resolved, delegate to the report renderer:

```js
if (window.StockLayerCompanyReport) {
  await window.StockLayerCompanyReport.open({ company, modal, title, subtitle, content });
  return;
}
```

The renderer reads the versioned `company-reports/{slug}.json` feeds from this repository and does not generate placeholder analysis in the browser.

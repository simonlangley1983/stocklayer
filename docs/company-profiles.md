# Company business descriptions

`company-profiles.json` contains original editorial paragraphs for all 100 members of `universes/uk-100/companies.json`, keyed by the existing stable company slug. Each entry records the source used for business facts and the editorial review date. Descriptions explain activities, customers, revenue and operating characteristics; they do not contain live valuations or investment recommendations.

`automation/build_company_reports.py` reads these profiles when building `company-reports/{slug}.json`. The description becomes `company.introduction`, with `introductionSources` and `introductionReviewedAt` retained alongside it. This keeps scheduled report rebuilds from replacing the paragraphs with generic listing information. Unknown companies retain the existing fallback until an editorial profile is added.

The report renderer in `site-assets/customer-report.js` prioritises the report introduction ahead of legacy one-line homepage taglines. Deploy that asset to the website together with data updates. The existing paragraph styling wraps the full text without truncation.

When the tracked universe changes, add a sourced profile under the new slug. Run `python -m unittest discover -s tests -p test_build_company_reports.py` to check coverage, paragraph length and rebuild preservation.

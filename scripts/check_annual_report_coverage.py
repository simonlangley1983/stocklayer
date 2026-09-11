"""Track real extracted annual-report coverage and alert on regressions/stalls."""
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def assess(companies, years, previous):
    old = {r["slug"]: r for r in previous.get("companies", [])}
    rows = []
    alerts = []
    for c in companies:
        slug = c["slug"]
        current = sorted(years.get(slug, set()))
        prior = old.get(slug)
        unchanged = prior is not None and current == prior["years"]
        stalled = prior.get("unchangedRuns", 0) + 1 if unchanged else 0
        row = {"slug": slug, "name": c.get("companyName", slug), "years": current,
               "unchangedRuns": stalled}
        rows.append(row)
        if prior and set(prior["years"]) - set(current):
            alerts.append(f"{slug}: previously extracted report years lost")
        if len(current) < 2 and stalled >= 3:
            alerts.append(f"{slug}: fewer than two report years, unchanged for {stalled} runs; review source availability")
    return {"companyCount": len(rows), "noReports": sum(not r["years"] for r in rows),
            "oneReport": sum(len(r["years"]) == 1 for r in rows),
            "multipleReports": sum(len(r["years"]) >= 2 for r in rows),
            "reportYears": sum(len(r["years"]) for r in rows), "companies": rows, "alerts": alerts}

def main():
    path = ROOT / "annual-reports/coverage-status.json"
    if "--enforce" in __import__("sys").argv:
        status = json.loads(path.read_text())
        for alert in status["alerts"]:
            print("::error::" + alert)
        return int(bool(status["alerts"]))
    previous = json.loads(path.read_text()) if path.exists() else {}
    years = {}
    for name in ("extracted-keywords-history.json", "extracted-keywords.json"):
        for r in json.loads((ROOT / "annual-reports" / name).read_text()).get("reports", []):
            if r.get("report_data_status") == "extracted" and r.get("company_slug") and r.get("report_year"):
                years.setdefault(r["company_slug"], set()).add(int(r["report_year"]))
    companies = json.loads((ROOT / "uk-companies.json").read_text())
    status = assess(companies, years, previous)
    from datetime import datetime, timezone
    status["checkedAt"] = datetime.now(timezone.utc).isoformat()
    text = "# Annual report coverage\n\n"
    text += f"Current companies: {status['companyCount']} | No reports: {status['noReports']} | One year: {status['oneReport']} | Multiple years: {status['multipleReports']}\n\n"
    text += f"Successfully extracted company/year pairs: {status['reportYears']}\n\n"
    text += "Counts represent extracted reports, not merely discovered links. A single year can be legitimate for a recently listed company; stalled coverage needs review.\n\n"
    text += "| Company | Extracted years | Unchanged runs |\n|---|---|---|\n"
    for row in status['companies']:
        text += f"| {row['name']} | {', '.join(map(str, row['years'])) or 'None'} | {row['unchangedRuns']} |\n"
    if status['alerts']:
        text += "\n## Needs attention\n" + "\n".join('- ' + a for a in status['alerts'])
    path.write_text(json.dumps(status, indent=2) + "\n")
    (ROOT / "annual-reports/coverage-summary.md").write_text(text, encoding="utf-8")
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a', encoding='utf-8') as f: f.write(text)
    print(text.split('\n\n')[1])
    return 0

if __name__ == '__main__':
    raise SystemExit(main())

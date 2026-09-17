"""Build an auditable annual-report register without inferring latest availability."""
import json
from pathlib import Path
from datetime import datetime, timezone
ROOT = Path(__file__).resolve().parents[1]
def build(companies, index, history, previous, state):
    rows = {}
    for company in companies:
        slug = company['slug']; indexed = index.get('companies', {}).get(slug, {})
        prior = previous.get('companies', {}).get(slug, {})
        reports = [r for r in history.get('reports', []) if r.get('company_slug') == slug and r.get('report_data_status') == 'extracted']
        latest = max((int(r['report_year']) for r in reports), default=None)
        # Previously successful discovery pages are evidence-backed, not guessed URLs.
        archives = list(prior.get('archiveUrls', []))
        for source in indexed.get('sources', []):
            if source.get('statusCode') == 200 and source.get('reportLinksFound', 0) > 0 and source.get('url'):
                archives.append(source['url'])
        archives = list(dict.fromkeys(archives))[:3]
        verified = prior.get('latestAvailableYear')
        reviewed = prior.get('latestAvailabilityVerifiedAt')
        fresh = False
        if reviewed:
            try: fresh = (datetime.now(timezone.utc) - datetime.fromisoformat(reviewed.replace('Z','+00:00'))).days < 30
            except (ValueError, TypeError): pass
        status = 'verified_current' if fresh and verified is not None and latest == verified else 'newer_report_needed' if verified and (latest or 0) < verified else 'latest_availability_unverified'
        rows[slug] = {**prior, 'companyName': company['companyName'], 'archiveUrls': archives,
            'archiveStatus': 'previously_successful' if archives else 'needs_source_review',
            'latestLoadedYear': latest, 'latestAvailableYear': verified,
            'latestAvailabilityVerifiedAt': reviewed, 'status': status,
            'documents': [{'year':r['report_year'], 'url':r.get('report_url'), 'sha256':r.get('source_sha256'), 'extractionStatus':'extracted'} for r in reports],
            'pendingCandidates': [{'year':r.get('year'), 'url':r.get('url'), 'discoveryStatus':'candidate_unverified'} for r in indexed.get('reports', []) if isinstance(r.get('year'),int) and r['year'] > (latest or 0)],
            'retryDiagnostics': {k:v for k,v in state.get('attempts', {}).items() if k.startswith(slug+':')}}
    return {'generatedAt':datetime.now(timezone.utc).isoformat(), 'companyCount':len(rows),
        'verifiedCurrentCount':sum(r['status']=='verified_current' for r in rows.values()), 'companies':rows}
def main():
    def load(p, default):
        path=ROOT/p
        return json.loads(path.read_text(encoding='utf-8')) if path.exists() else default
    universe=load('uk-companies.json',[])
    companies=universe if isinstance(universe,list) else universe['companies']
    register=build(companies,load('annual-reports/reports-index.json',{}),load('annual-reports/extracted-keywords-history.json',{}),load('annual-reports/report-register.json',{}),load('annual-reports/backfill-state.json',{}))
    (ROOT/'annual-reports/report-register.json').write_text(json.dumps(register,indent=2)+'\n',encoding='utf-8')
if __name__=='__main__': main()

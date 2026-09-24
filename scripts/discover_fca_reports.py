"""Add FCA National Storage Mechanism annual-report candidates to the local index."""
import json, re, urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]; API='https://api.data.fca.org.uk/search?index=nsm-search'
def norm(value): return re.sub(r'\b(plc|p\.l\.c|group|holdings|limited|ltd|public|company)\b|[^a-z0-9]','',str(value).lower())
companies_path = ROOT / 'uk-companies.json'
if not companies_path.exists():
    companies_path = ROOT / 'ftse100.json'
companies=json.loads(companies_path.read_text()); names={norm(c['companyName']):c for c in companies}
index_path=ROOT/'annual-reports/reports-index.json'; index=json.loads(index_path.read_text()); added=[]
for year in range(2020,2026):
 body={"from":0,"size":4000,"criteriaObj":{"criteria":[{"name":"type_code","value":["ACS"]}],"dateCriteria":[{"name":"document_date","value":{"from":f"01/01/{year}","to":f"31/12/{year}"}}]}}
 req=urllib.request.Request(API,data=json.dumps(body).encode(),headers={'Content-Type':'application/json'})
 rows=json.load(urllib.request.urlopen(req,timeout=30))['hits']['hits']
 for row in rows:
  source=row['_source']; company=names.get(norm(source.get('company','')))
  link=source.get('download_link','')
  if not company or not link or not link.lower().endswith(('.pdf','.xhtml','.html')): continue
  record=index['companies'].setdefault(company['slug'],{'slug':company['slug'],'companyName':company['companyName'],'ticker':company['ticker'],'reports':[]})
  url='https://data.fca.org.uk/artefacts/'+link.lstrip('/')
  if not any(item.get('year')==year and item.get('url')==url for item in record.setdefault('reports',[])):
   record['reports'].append({'year':year,'title':source.get('headline') or 'FCA Annual Financial Report','url':url,'sourceUrl':'https://data.fca.org.uk/#/nsm/nationalstoragemechanism','discoveryStatus':'fca_nsm_candidate'})
   added.append({'slug':company['slug'],'year':year,'url':url})
index['fcaNsmLastCheckedAt']=datetime.now(timezone.utc).isoformat();index['fcaNsmCandidatesAdded']=len(added)
index_path.write_text(json.dumps(index,indent=2)+'\n');(ROOT/'outputs/fca-nsm-discovery.json').write_text(json.dumps({'added':added},indent=2)+'\n')
print(f'Added {len(added)} FCA candidates')

"""Incremental annual-report extraction with a durable, fair retry queue."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta
import hashlib
import json
import os
from pathlib import Path
import re
import urllib.parse
import subprocess
import sys

from extract_annual_report_keywords import extract_group, is_known_non_report_candidate

ROOT = Path(__file__).resolve().parents[1]


def read(path, default=None):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temp.replace(path)


def queue_groups(index, companies, existing, state, start_year, end_year, now):
    groups = []
    known = {c["slug"]: c for c in companies}
    for slug, indexed in index.get("companies", {}).items():
        if slug not in known:
            continue
        company = {**indexed, **known[slug]}
        by_year = {}
        for candidate in indexed.get("reports", []):
            filename = urllib.parse.unquote(Path(urllib.parse.urlparse(candidate.get("url", "")).path).name).lower()
            normalised = re.sub(r"[_-]+", " ", filename)
            if any(term in normalised for term in (
                "modern slavery", "remuneration policy", "172 statement", "estma",
                "data report", "esg addendum", "payments to government", "tax strategy",
            )):
                continue
            year = candidate.get("year")
            filename_years = set(re.findall(r"(?<!\d)(20\d{2})(?!\d)", filename))
            if len(filename_years) == 1:
                year = int(next(iter(filename_years)))
            if isinstance(year, int) and start_year <= year <= end_year and not is_known_non_report_candidate(candidate):
                by_year.setdefault(year, []).append(candidate)
        for year, candidates in by_year.items():
            key = f"{slug}:{year}"
            if key in existing:
                continue
            fingerprint = hashlib.sha256(json.dumps(sorted({r['url'] for r in candidates})).encode()).hexdigest()
            prior = state.get(key, {})
            changed = prior.get("candidateHash") != fingerprint
            if not changed and prior.get("retryAfter", "") > now.isoformat():
                continue
            # Never-tried groups go first; failed groups rotate by last attempt.
            groups.append((company, year, candidates, key, fingerprint, prior if not changed else {}))
    return sorted(groups, key=lambda g: (g[5].get("lastAttempt", ""), -g[1], g[3]))


def run_group(group, timeout):
    company, year, candidates = group[:3]
    try:
        result = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), "--worker"],
            input=json.dumps([company, year, candidates]), text=True, encoding="utf-8",
            capture_output=True, timeout=timeout,
        )
        if result.returncode:
            raise RuntimeError(result.stderr[-1500:])
        record = json.loads(result.stdout)
        if record.get("report_data_status") == "extracted" and int(record.get("report_year", 0)) != year:
            raise ValueError(f"Requested {year}, document identifies {record.get('report_year')}; needs source review")
        return record
    except Exception as error:
        return {"company_slug": company["slug"], "ticker": company.get("ticker"),
                "report_year": year, "report_data_status": "extraction_failed",
                "attempts": [{"error": f"{type(error).__name__}: {error}"}]}


def main():
    if "--worker" in sys.argv:
        company, year, candidates = json.load(sys.stdin)
        print(json.dumps(extract_group(company, year, candidates, 20)))
        return 0
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch-size", type=int, default=30)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--report-timeout", type=int, default=180)
    parser.add_argument("--start-year", type=int, default=2020)
    parser.add_argument("--end-year", type=int, default=datetime.now(timezone.utc).year)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if min(args.batch_size, args.workers, args.report_timeout) < 1:
        parser.error("batch size, workers and timeout must be positive")
    directory = ROOT / "annual-reports"
    history_path = directory / "extracted-keywords-history.json"
    history = read(history_path, {"reports": []})
    existing = {}
    for payload in (history, read(directory / "extracted-keywords.json", {"reports": []})):
        for record in payload.get("reports", []):
            if record.get("report_data_status") == "extracted":
                existing.setdefault(f"{record['company_slug']}:{record['report_year']}", record)
    companies = read(ROOT / "uk-companies.json")
    if not companies or len({c['slug'] for c in companies}) != 100:
        raise ValueError("Expected the current 100-company universe")
    state_path = directory / "backfill-state.json"
    state = read(state_path, {"attempts": {}})
    now = datetime.now(timezone.utc)
    pending = queue_groups(read(directory / "reports-index.json"), companies, existing,
                           state["attempts"], args.start_year, args.end_year, now)
    batch = pending[:args.batch_size]
    print(f"Eligible pending groups: {len(pending)}; this batch: {len(batch)}", flush=True)
    if args.dry_run:
        print(json.dumps([g[3] for g in batch]))
        return 0
    recovered = 0
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {executor.submit(run_group, group, args.report_timeout): group for group in batch}
        for future in as_completed(futures):
            group = futures[future]
            record = future.result()
            key = group[3]
            success = record.get("report_data_status") == "extracted"
            finished = datetime.now(timezone.utc)
            attempts = group[5].get("attemptCount", 0) + 1
            state["attempts"][key] = {
                "candidateHash": group[4], "attemptCount": attempts,
                "lastAttempt": finished.isoformat(), "status": record.get("report_data_status"),
                "retryAfter": (finished + timedelta(hours=min(168, 6 * 2 ** min(attempts - 1, 5)))).isoformat(),
                "diagnostics": record.get("attempts", []),
            }
            if success:
                existing[key] = record
                recovered += 1
                history.update({"generated_at": finished.isoformat(),
                                "reports": sorted(existing.values(), key=lambda r: (r['company_slug'], r['report_year'])),
                                "group_count": len(existing), "extracted_count": len(existing), "failed_count": 0})
                save(history_path, history)
            save(state_path, state)
            print(f"{key}: {record.get('report_data_status')}", flush=True)
    target_end = min(args.end_year, datetime.now(timezone.utc).year - 1)
    missing = {c['slug']: [y for y in range(args.start_year, target_end + 1)
                           if f"{c['slug']}:{y}" not in existing] for c in companies}
    summary = {"checkedAt": datetime.now(timezone.utc).isoformat(), "targetStartYear": args.start_year,
               "targetEndYear": target_end, "attempted": len(batch), "recovered": recovered,
               "missingCompanyYears": sum(map(len, missing.values())), "missingYearsByCompany": missing,
               "note": "Dataset gaps, not proof of publication availability; newer issuers may not have all target years."}
    save(directory / "backfill-progress.json", summary)
    message = f"Annual report backfill: {recovered} recovered from {len(batch)} attempts; {summary['missingCompanyYears']} gaps remain for {args.start_year}-{target_end}."
    print(message)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a', encoding='utf-8') as stream:
            stream.write(message + '\n')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Audit the rolling 90-day news history and durably recover small missing windows."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from datetime import date, datetime, timedelta, timezone

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from automation import news_sentiment as news

STATE_PATH = news.SENTIMENT_DIR / "recovery-state.json"
COVERAGE_PATH = news.SENTIMENT_DIR / "coverage-status.json"


def load_histories(companies):
    return {c["slug"]: news.read_json(news.HISTORY_DIR / f"{c['slug']}.json", {}).get("observations", []) for c in companies}


def audit(companies, histories, end_day, lookback=90):
    start = end_day - timedelta(days=lookback - 1)
    dates = [(start + timedelta(days=i)).isoformat() for i in range(lookback)]
    rows = []
    for company in companies:
        by_date = {o["date"]: o for o in histories.get(company["slug"], []) if o.get("date")}
        complete = {d for d in dates if news.observation_complete(by_date.get(d, {}))}
        missing = [d for d in dates if d not in complete]
        rows.append({"slug": company["slug"], "companyName": company["companyName"],
                     "completedDays": len(complete), "missingDates": missing,
                     "scoredDays": sum(by_date[d].get("coverageStatus") == "ok" for d in complete),
                     "noNewsDays": sum(by_date[d].get("coverageStatus") == "no_coverage" for d in complete),
                     "partialDays": sum(d in by_date and by_date[d].get("coverageStatus") == "ok" for d in missing),
                     "latestDayComplete": end_day.isoformat() in complete})
    total = len(companies) * lookback
    completed = sum(r["completedDays"] for r in rows)
    return {"checkedAt": datetime.now(timezone.utc).isoformat(),
            "startDate": start.isoformat(), "endDate": end_day.isoformat(),
            "companyCount": len(companies), "requestedCompanyDays": total,
            "completedCompanyDays": completed, "pendingCompanyDays": total - completed,
            "completionPercent": round(100 * completed / total, 2) if total else 100,
            "latestDayCompletedCompanies": sum(r["latestDayComplete"] for r in rows),
            "scoredCompanyDays": sum(r["scoredDays"] for r in rows),
            "noNewsCompanyDays": sum(r["noNewsDays"] for r in rows), "companies": rows}


def plan_windows(coverage, state, limit=12, window_days=5):
    """Fair rotation prevents a blocked issuer from consuming every recovery run."""
    attempts = state.get("lastAttemptAt", {})
    rows = sorted((r for r in coverage["companies"] if r["missingDates"]),
                  key=lambda r: (attempts.get(r["slug"], ""), r["missingDates"][0], r["slug"]))
    plan = []
    for row in rows[:limit]:
        missing = set(row["missingDates"])
        start = date.fromisoformat(row["missingDates"][0])
        end = start
        while (end - start).days + 1 < window_days and (end + timedelta(days=1)).isoformat() in missing:
            end += timedelta(days=1)
        plan.append({"slug": row["slug"], "start": start.isoformat(), "end": end.isoformat(),
                     "days": (end - start).days + 1})
    return plan


def save_audit(companies, end_day):
    coverage = audit(companies, load_histories(companies), end_day)
    news.write_json_atomic(COVERAGE_PATH, coverage)
    return coverage


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit-only", action="store_true")
    parser.add_argument("--max-windows", type=int, default=12)
    parser.add_argument("--max-runtime-seconds", type=int, default=1800)
    args = parser.parse_args()
    if not 1 <= args.max_windows <= 12 or args.max_runtime_seconds < 120:
        parser.error("Use 1-12 windows and a runtime budget of at least 120 seconds")
    companies = news.read_json(news.UNIVERSE_PATH, {})["companies"]
    end_day = datetime.now(news.LONDON).date() - timedelta(days=1)
    coverage = save_audit(companies, end_day)
    state = news.read_json(STATE_PATH, {"lastAttemptAt": {}})
    if not args.audit_only:
        initial = coverage["completedCompanyDays"]
        deadline = time.monotonic() + args.max_runtime_seconds
        state["lastStartedAt"] = datetime.now(timezone.utc).isoformat()
        state["attempts"] = []
        news.write_json_atomic(STATE_PATH, state)
        for window in plan_windows(coverage, state, args.max_windows):
            remaining = deadline - time.monotonic()
            if remaining < 120:
                break
            stamp = datetime.now(timezone.utc).isoformat()
            state["lastAttemptAt"][window["slug"]] = stamp
            news.write_json_atomic(STATE_PATH, state)
            command = [sys.executable, str(news.ROOT / "automation/news_sentiment.py"),
                       "--slugs", window["slug"], "--date", window["end"],
                       "--backfill-days", str(window["days"]), "--request-delay", "60",
                       "--max-runtime-seconds", str(min(300, int(remaining) - 90))]
            try:
                result = subprocess.run(command, cwd=news.ROOT, timeout=min(600, remaining), check=False)
                code = result.returncode
            except subprocess.TimeoutExpired:
                code = 124
            state["attempts"].append({**window, "startedAt": stamp, "exitCode": code})
            # Every finished window is checkpointed before the next one starts.
            coverage = save_audit(companies, end_day)
            news.write_json_atomic(STATE_PATH, state)
        recovered = coverage["completedCompanyDays"] - initial
        state["lastCompletedAt"] = datetime.now(timezone.utc).isoformat()
        state["lastRecoveredCompanyDays"] = recovered
        state["consecutiveNoProgressRuns"] = (state.get("consecutiveNoProgressRuns", 0) + 1
                                              if state["attempts"] and not recovered else 0)
        if recovered:
            state["lastProgressAt"] = state["lastCompletedAt"]
        news.write_json_atomic(STATE_PATH, state)
        # Publish overall coverage, not the final window's misleading 100% status.
        news.run(argparse.Namespace(universe=news.UNIVERSE_PATH, slugs=None,
                  date=end_day.isoformat(), backfill_days=90, request_delay=60,
                  rebuild_only=True, test_scorer=False, dry_run=False, minimum_completeness=.9))
    text = (f"## Press collection coverage\n\n"
            f"Period: {coverage['startDate']} to {coverage['endDate']}\n\n"
            f"Completed: {coverage['completedCompanyDays']}/{coverage['requestedCompanyDays']} company-days "
            f"({coverage['completionPercent']}%). Pending: {coverage['pendingCompanyDays']}.\n\n"
            f"With eligible news: {coverage['scoredCompanyDays']}. Successful checks with no eligible news: "
            f"{coverage['noNewsCompanyDays']}. Latest day checked: {coverage['latestDayCompletedCompanies']}/"
            f"{coverage['companyCount']} companies.\n")
    (news.SENTIMENT_DIR / "coverage-summary.md").write_text(text, encoding="utf-8")
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as stream:
            stream.write(text)
    print(text)
    if state.get("consecutiveNoProgressRuns", 0) >= 3:
        print("::warning::News recovery has made no progress for three runs; provider access needs attention.")
        if not args.audit_only:
            return 2
    if not args.audit_only and any(a["exitCode"] not in {0, 2, 124} for a in state.get("attempts", [])):
        print("::error::A recovery worker failed unexpectedly; available progress has been saved.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

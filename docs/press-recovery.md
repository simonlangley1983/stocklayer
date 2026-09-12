# Press collection recovery

The daily news workflow still collects the previous London calendar day. An
additional run at 17 minutes past every second hour resumes missing history across the
rolling 90-day chart period. All news jobs share one concurrency group so they
do not compete for GDELT capacity.

Recovery processes at most 12 company/date windows per run. Each window contains
at most five consecutive missing days, with 90 seconds between provider requests.
A 30-minute collection budget leaves time for aggregate rebuilding and publishing.
Company history and recovery state are saved after each window. Provider errors,
timeouts and truncated responses remain retryable; a successful search with no
eligible stories counts as a completed check, not a neutral sentiment score.

The queue is derived from stored observations. `sentiment/recovery-state.json`
records the last attempt per company so repeatedly blocked companies do not
starve the rest. Completed dates are skipped on subsequent runs.

`sentiment/coverage-status.json` and `sentiment/coverage-summary.md` report full
coverage independently of any one small job's success. They distinguish scored
days, successful searches without eligible news, and unfinished company-days.
The workflow summary displays these totals. Three recovery runs with no progress
fail the run's health check rather than silently implying collection is healthy.

The Codex task has a separate four-hour watchdog that checks GitHub progress,
restarts missed recovery runs without duplicating active work, and notifies the
user of stalls or completion. This additional check depends on the Codex
automation host being available; neither scheduler nor external news access has
an absolute availability guarantee.

Manual resume:

```sh
gh workflow run daily-news-sentiment.yml --repo simonlangley1983/stocklayer -f recovery=true
```

Local read-only collection audit (writes only the coverage report):

```sh
python automation/news_recovery.py --audit-only
```

The recovery window moves with the chart's 90-day period. Recovery cannot
guarantee that a provider still supplies older material. It never fills failed
collection days with invented scores. All previously stored history is retained
under the sentiment methodology's existing retention policy.

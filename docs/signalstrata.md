# SignalStrata strategic intelligence

StockLayer stores SignalStrata data as static JSON in `strategic-intelligence/`. The browser only fetches the selected company's file and never calls SignalStrata directly.

## Configuration

Create the repository secret `SIGNALSTRATA_API_KEY` in GitHub. Do not add the key to a source file, client-side JavaScript, or a GitHub variable.

The `Refresh SignalStrata strategic intelligence` workflow runs at 03:00 Europe/London each day. GitHub Actions cron is UTC, so the workflow starts at both 02:00 and 03:00 UTC and the script exits cleanly unless it is 03:00 in London. This keeps the schedule aligned through daylight saving time. It can also be started manually with `workflow_dispatch`.

The workflow reads StockLayer's `ftse100.json` universe, derives API tickers from `lseTicker` where possible, and applies `strategic-intelligence/ticker-map.json` only for exceptions.

## Data guarantees

The refresh script uses batches of up to 20 tickers, request timeouts, `Retry-After`, and exponential backoff. It writes files atomically and never replaces a valid company file with an invalid response. It records the actual successful retrieval time separately from the latest evidence date.

Initiatives are only merged when the provider gives the same initiative ID. Evidence is de-duplicated by an ID or a stable title/date/source key. Near-duplicate initiatives without a reliable common ID are retained intentionally.

## Attribution asset

The supplied official SignalStrata logo is stored at `images/signalstrata-logo.png`. StockLayer does not recreate or download a substitute logo.

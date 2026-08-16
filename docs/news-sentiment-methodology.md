# StockLayer daily news sentiment methodology

Status: methodology v1.0, approved implementation baseline

Scope: all companies in `sentiment/company-universe.json`

Display integration: deliberately out of scope for this change

## 1. Purpose

StockLayer's news sentiment layer measures the tone of eligible press coverage about each tracked company. It is a research signal describing the current press narrative. It is not a share-price forecast, investment recommendation, or substitute for reading the underlying coverage.

The system produces two related values for every company and London calendar day:

- **Daily sentiment score (0-100 or null):** the confidence-adjusted tone of eligible coverage published that day.
- **Rolling sentiment score (0-100):** a smoothed ongoing measure that reacts to new coverage and fades toward neutral when coverage goes quiet.

A score of 50 is neutral. Scores above 50 are positive and scores below 50 are negative. A missing daily score means there was no eligible coverage or collection failed; it must never be silently converted into neutral coverage.

## 2. Coverage universe and day boundary

- The initial universe is the 100 companies currently tracked by StockLayer UK.
- The machine-readable universe is `sentiment/company-universe.json` and includes the company name, ticker, slug, sector, domain, search aliases and query context.
- A day is `00:00:00` through `23:59:59` in `Europe/London`, including daylight-saving changes.
- The scheduled run processes the previous completed London day. Manual runs can specify another date or a subset of companies.
- Only English-language coverage is scored in v1. Non-English support requires a separately validated multilingual model.

## 3. Collection and evidence

The collector is provider-based. V1 uses the public GDELT DOC 2.0 article search endpoint and stores only the minimum evidence needed to explain a score: title, canonical URL, publisher domain and publication time. It does not copy or republish article bodies.

The provider currently requires requests to be spaced at least five seconds apart. The workflow uses 5.25 seconds, processes companies sequentially and records rate-limit failures rather than retrying aggressively. At 100 companies, provider pacing alone takes about nine minutes.

Provider results are candidates, not automatically eligible evidence. Candidates pass the following gates:

1. The publication time falls inside the London day being scored.
2. The item has a usable headline, URL and publisher domain.
3. The company query or configured alias/context identifies the tracked company with sufficient specificity.
4. The item is not a duplicate, stale update, market-data page, navigation page, video-only page, social post, press-release mirror or obvious spam.
5. The language is English where the provider supplies language metadata.

Company announcements and press releases may be retained when independently carried by a news publisher, but first-party corporate domains are labelled `first_party` and excluded from the press score by default. They can still generate factual event flags.

## 4. Deduplication and source treatment

Wire stories and syndicated headlines can otherwise swamp a daily average. StockLayer therefore:

- canonicalises URLs by removing fragments and common tracking parameters;
- collapses exact canonical-URL duplicates;
- normalises headlines and clusters headlines with token similarity of at least 0.82;
- gives each story cluster a maximum total score weight of 1.0;
- averages distinct publisher versions within a cluster instead of counting each copy as a new story;
- caps any one publisher domain at 35% of the day's total effective weight.

V1 does not assign ideological or quality scores to publishers. It records source diversity and uses an explicit blocklist only for sources that are technically invalid, spammy, first-party or repeatedly misattributed.

## 5. Article sentiment

Eligible text is the headline plus a provider-supplied description when one is legally available. Article bodies are not fetched in v1. This boundary makes the measure reproducible and avoids pretending that inaccessible paywalled text was analysed.

English financial sentiment is classified with `ProsusAI/finbert`, pinned in the machine methodology. The model returns probabilities for positive, neutral and negative. Article polarity is:

`polarity = P(positive) - P(negative)`

The result is in `[-1, 1]`. Model probabilities, input type and model revision are retained for audit. Items with low entity relevance remain visible as rejected candidates and do not affect the score.

## 6. Daily score

Each unique story cluster contributes its mean article polarity. The raw day polarity is the weighted mean across clusters after the publisher cap. To stop one dramatic headline producing an extreme score, the mean is shrunk toward neutral with two neutral-equivalent prior stories:

`daily_polarity = sum(weight * cluster_polarity) / (sum(weight) + 2)`

`daily_score = round(50 + 50 * daily_polarity, 1)`

The daily score is null when there are no eligible clusters. It is never filled with 50.

Daily labels are:

| Score | Label |
|---:|---|
| 0-24.9 | Very negative |
| 25-39.9 | Negative |
| 40-44.9 | Slightly negative |
| 45-55 | Neutral / mixed |
| 55.1-60 | Slightly positive |
| 60.1-75 | Positive |
| 75.1-100 | Very positive |

## 7. Confidence

Confidence describes evidence sufficiency, not certainty that the market will agree. It combines:

- effective unique story count;
- number of distinct publisher domains;
- mean entity relevance;
- share of stories with decisive rather than neutral model probabilities;
- collection completeness.

The base formula is deterministic and bounded to 0-100:

`coverage = 1 - exp(-effective_story_count / 3)`

`diversity = min(1, source_count / 3)`

`confidence = 100 * coverage * (0.55 + 0.45 * diversity) * mean_relevance * completeness`

Rounded confidence bands are low (0-39), medium (40-69) and high (70-100). Large-change and generic good/bad-press flags require confidence of at least 45, at least three unique story clusters and at least two publisher domains.

## 8. Rolling score

The rolling score is an exponentially weighted narrative measure with a seven-day half-life:

- Between observations, the previous rolling polarity decays toward zero (score 50).
- On a covered day, the decayed previous polarity is blended with the daily polarity.
- The new-day blend is scaled by confidence, so thin coverage cannot whip the rolling score around.
- With no history the rolling score starts at 50.

The precise implementation is versioned in `automation/news_sentiment.py`; changing it requires a methodology-version increment and a documented backfill decision.

## 9. Event and monitoring flags

Flags are deterministic, evidence-linked and separate from model sentiment. A flag records type, direction, severity, confidence and the story IDs supporting it.

### Narrative movement

- `sentiment_jump`: daily score is at least 12 points above the previous scored day and confidence gates pass.
- `sentiment_drop`: daily score is at least 12 points below the previous scored day and confidence gates pass.
- `good_press`: score is at least 65 and confidence gates pass.
- `bad_press`: score is at most 35 and confidence gates pass.
- `coverage_spike`: unique story volume is at least three times the trailing 20-scored-day median and at least five stories.
- `high_disagreement`: both positive and negative stories are present and polarity dispersion is at least 0.55.
- `low_confidence`: a score exists but confidence is below 40.
- `collection_degraded`: one or more provider requests failed or completeness is below 90%.
- `no_coverage`: collection succeeded but no eligible article was found.

### Company events

- `annual_results`, `interim_results`, `trading_update`
- `earnings_beat`, `earnings_miss`, `profit_warning`
- `guidance_raised`, `guidance_cut`
- `dividend_change`, `share_buyback`, `capital_raise`
- `merger_acquisition`, `divestment`
- `leadership_change`, `workforce_reduction`, `industrial_action`
- `regulatory_investigation`, `fine_or_lawsuit`
- `cybersecurity_incident`, `operational_incident`, `product_recall`
- `analyst_upgrade`, `analyst_downgrade`

Phrase matching is defined in `sentiment/event-rules.json`. Event flags require at least one supporting eligible headline and are labelled `possible` unless supported by two publisher domains or a first-party/regulatory source. The UI must link back to evidence and avoid presenting a phrase match as confirmed fact.

## 10. Stored data and retention

- `sentiment/latest.json`: compact current summary for every company.
- `sentiment/history/{slug}.json`: up to 400 daily observations per company.
- `sentiment/run-status.json`: run-level monitoring, provider failures and per-company status.
- Each daily observation retains up to five representative story links, never article bodies.
- URLs, titles and publisher domains are evidence metadata and may be removed on request or when invalid.

Writes are atomic. A provider failure must not erase the prior latest score. Re-running the same date replaces that date rather than appending a duplicate.

## 11. Validation and governance

Before site integration:

1. Run a minimum 30-day backfill for all companies.
2. Manually review a stratified sample of at least 300 candidate headlines, including ambiguous company names, positive/negative extremes and every event type observed.
3. Report precision for entity relevance, duplicate-cluster quality and each event flag; do not enable a flag whose reviewed precision is below 80%.
4. Compare daily score stability with and without the largest publisher and largest story cluster.
5. Record provider coverage gaps and false-positive aliases in configuration.
6. Freeze the model identifier, model revision, thresholds and methodology version used for the public series.

Any material change to provider, model, entity rules, score formula or thresholds creates a new methodology version. Historical values must either remain on their original version or be explicitly backfilled; silent rewriting is prohibited.

Backfills fetch five-day windows with up to 250 candidates. A window that reaches that ceiling is recursively split until the result is below the ceiling or a single London day remains. A still-truncated single day is retained with reduced confidence and a `collection_degraded` flag rather than being presented as complete.

## 12. Known limitations

- Headline/description sentiment is not full-article sentiment.
- FinBERT can classify general financial tone but cannot reliably infer every company-specific second-order effect.
- Coverage volume and publisher availability are unequal across companies.
- Paywalls, corrections, timestamp errors and syndication can affect the evidence set.
- Event phrase rules identify possible events and require source review.
- Sentiment can be accurate as a description of coverage and still have no predictive value for share prices.

These limitations must be visible wherever the score is eventually presented.

## 13. Technical references

- [GDELT DOC 2.0 API overview](https://blog.gdeltproject.org/gdelt-doc-2-0-api-debuts/)
- [FinBERT paper](https://arxiv.org/abs/1908.10063)
- [ProsusAI/finbert model card](https://huggingface.co/ProsusAI/finbert)

The production model is pinned to Hugging Face revision `4556d13015211d73dccd3fdd39d39232506f3e43` rather than the mutable `main` revision.

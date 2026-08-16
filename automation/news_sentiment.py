"""Collect, score and persist StockLayer daily company news sentiment."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import statistics
import sys
import tempfile
import time
from collections import Counter, defaultdict
from datetime import date, datetime, time as datetime_time, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Protocol
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


ROOT = Path(__file__).resolve().parents[1]
SENTIMENT_DIR = ROOT / "sentiment"
UNIVERSE_PATH = SENTIMENT_DIR / "company-universe.json"
METHODOLOGY_PATH = SENTIMENT_DIR / "methodology.json"
EVENT_RULES_PATH = SENTIMENT_DIR / "event-rules.json"
LATEST_PATH = SENTIMENT_DIR / "latest.json"
RUN_STATUS_PATH = SENTIMENT_DIR / "run-status.json"
HISTORY_DIR = SENTIMENT_DIR / "history"
LONDON = ZoneInfo("Europe/London")
GDELT_ENDPOINT = "https://api.gdeltproject.org/api/v2/doc/doc"
TRACKING_PARAMETERS = {
    "fbclid",
    "gclid",
    "mc_cid",
    "mc_eid",
    "ref",
    "referrer",
    "source",
}
TOKEN_RE = re.compile(r"[a-z0-9]+")


class SentimentScorer(Protocol):
    model_id: str

    def score(self, texts: list[str]) -> list[dict[str, float]]: ...


class FinBertScorer:
    def __init__(self, model_id: str, revision: str = "main") -> None:
        from transformers import pipeline

        self.model_id = model_id
        self.revision = revision
        self._classifier = pipeline(
            "text-classification",
            model=model_id,
            revision=revision,
            tokenizer=model_id,
            top_k=None,
            device=-1,
        )

    def score(self, texts: list[str]) -> list[dict[str, float]]:
        if not texts:
            return []
        raw = self._classifier(
            texts,
            truncation=True,
            max_length=256,
            batch_size=16,
        )
        results: list[dict[str, float]] = []
        for item in raw:
            probabilities = {"positive": 0.0, "neutral": 0.0, "negative": 0.0}
            for label_score in item:
                label = str(label_score["label"]).lower()
                if label in probabilities:
                    probabilities[label] = float(label_score["score"])
            results.append(probabilities)
        return results


class KeywordTestScorer:
    """Deterministic local scorer used only by tests and smoke checks."""

    model_id = "keyword-test-scorer"
    POSITIVE = {"beat", "beats", "growth", "raised", "upgrade", "record", "wins"}
    NEGATIVE = {"cut", "cuts", "warning", "fall", "falls", "lawsuit", "breach", "miss"}

    def score(self, texts: list[str]) -> list[dict[str, float]]:
        results = []
        for text in texts:
            tokens = set(TOKEN_RE.findall(text.casefold()))
            positive = len(tokens & self.POSITIVE)
            negative = len(tokens & self.NEGATIVE)
            if positive > negative:
                results.append({"positive": 0.82, "neutral": 0.13, "negative": 0.05})
            elif negative > positive:
                results.append({"positive": 0.05, "neutral": 0.13, "negative": 0.82})
            else:
                results.append({"positive": 0.1, "neutral": 0.8, "negative": 0.1})
        return results


def read_json(path: Path, fallback: Any) -> Any:
    if not path.exists():
        return fallback
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Could not read valid JSON from {path}: {exc}") from exc


def write_json_atomic(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", newline="\n", dir=path.parent, delete=False
    ) as handle:
        handle.write(rendered)
        temporary = Path(handle.name)
    os.replace(temporary, path)


def canonical_url(value: str) -> str:
    try:
        parts = urlsplit(value.strip())
    except ValueError:
        return value.strip()
    query = [
        (key, item)
        for key, item in parse_qsl(parts.query, keep_blank_values=True)
        if not key.casefold().startswith("utm_") and key.casefold() not in TRACKING_PARAMETERS
    ]
    host = (parts.hostname or "").casefold()
    if parts.port and parts.port not in {80, 443}:
        host = f"{host}:{parts.port}"
    path = re.sub(r"/{2,}", "/", parts.path or "/").rstrip("/") or "/"
    return urlunsplit((parts.scheme.casefold() or "https", host, path, urlencode(query), ""))


def normalise_text(value: str) -> str:
    return " ".join(TOKEN_RE.findall(value.casefold()))


def headline_tokens(value: str) -> set[str]:
    return set(TOKEN_RE.findall(value.casefold()))


def jaccard(left: set[str], right: set[str]) -> float:
    union = left | right
    return len(left & right) / len(union) if union else 1.0


def parse_provider_datetime(value: str) -> datetime | None:
    formats = (
        "%Y%m%dT%H%M%SZ",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S%z",
    )
    for format_string in formats:
        try:
            parsed = datetime.strptime(value, format_string)
            return parsed.replace(tzinfo=parsed.tzinfo or timezone.utc).astimezone(timezone.utc)
        except ValueError:
            continue
    return None


def day_bounds(day: date) -> tuple[datetime, datetime]:
    start = datetime.combine(day, datetime_time.min, tzinfo=LONDON)
    return start.astimezone(timezone.utc), (start + timedelta(days=1)).astimezone(timezone.utc)


def make_session() -> requests.Session:
    session = requests.Session()
    retries = Retry(
        total=3,
        backoff_factor=1.0,
        status_forcelist=(500, 502, 503, 504),
        allowed_methods=("GET",),
    )
    session.mount("https://", HTTPAdapter(max_retries=retries))
    session.headers.update({"User-Agent": "StockLayerNewsSentiment/1.0"})
    return session


class GdeltProvider:
    name = "gdelt-doc-v2"

    def __init__(
        self,
        max_records: int = 50,
        request_delay: float = 5.25,
        rate_limit_retries: int = 4,
    ) -> None:
        self.max_records = max_records
        self.request_delay = request_delay
        self.rate_limit_retries = rate_limit_retries
        self.session = make_session()
        self.request_count = 0

    @staticmethod
    def query_for(company: dict[str, Any]) -> str:
        aliases = [item.replace('"', "") for item in company.get("aliases", [])]
        if company.get("requireHeadlineAlias"):
            specific = [item for item in aliases if len(TOKEN_RE.findall(item.casefold())) >= 2]
            aliases = specific or aliases
        aliases = aliases[:4]
        quoted = " OR ".join(f'"{item}"' for item in aliases if item)
        return f"({quoted}) sourcelang:english"

    def fetch(
        self, company: dict[str, Any], start_utc: datetime, end_utc: datetime
    ) -> list[dict[str, Any]]:
        params = {
            "query": self.query_for(company),
            "mode": "ArtList",
            "format": "json",
            "sort": "DateDesc",
            "maxrecords": str(self.max_records),
            "startdatetime": start_utc.strftime("%Y%m%d%H%M%S"),
            "enddatetime": end_utc.strftime("%Y%m%d%H%M%S"),
        }
        response = None
        for attempt in range(self.rate_limit_retries + 1):
            try:
                self.request_count += 1
                response = self.session.get(GDELT_ENDPOINT, params=params, timeout=40)
                if response.status_code == 429 and attempt < self.rate_limit_retries:
                    retry_after = response.headers.get("Retry-After")
                    try:
                        server_delay = float(retry_after) if retry_after else 0.0
                    except ValueError:
                        server_delay = 0.0
                    cooldown = max(server_delay, min(120.0, 15.0 * (2**attempt)))
                    print(
                        f"GDELT rate limited request; retrying in {cooldown:.0f}s "
                        f"({attempt + 1}/{self.rate_limit_retries})",
                        file=sys.stderr,
                        flush=True,
                    )
                    time.sleep(cooldown)
                    continue
                response.raise_for_status()
                try:
                    payload = response.json()
                except requests.exceptions.JSONDecodeError as exc:
                    excerpt = re.sub(r"\s+", " ", response.text).strip()[:240]
                    raise RuntimeError(
                        f"GDELT returned non-JSON content (HTTP {response.status_code}): {excerpt}"
                    ) from exc
                break
            finally:
                time.sleep(self.request_delay)
        else:  # pragma: no cover - the final attempt raises before this branch
            raise RuntimeError("GDELT request retry loop ended without a response")
        items = payload.get("articles", []) if isinstance(payload, dict) else []
        return [item for item in items if isinstance(item, dict)]


def article_id(article: dict[str, Any]) -> str:
    identity = f"{article.get('url', '')}\n{article.get('title', '')}".encode("utf-8")
    return hashlib.sha256(identity).hexdigest()[:16]


def domain_from_url(value: str) -> str:
    try:
        return (urlsplit(value).hostname or "").casefold().removeprefix("www.")
    except ValueError:
        return ""


def alias_in_text(aliases: Iterable[str], text: str) -> bool:
    normalised = f" {normalise_text(text)} "
    return any(f" {normalise_text(alias)} " in normalised for alias in aliases if alias)


def prepare_articles(
    candidates: list[dict[str, Any]],
    company: dict[str, Any],
    start_utc: datetime,
    end_utc: datetime,
) -> tuple[list[dict[str, Any]], Counter[str]]:
    accepted: list[dict[str, Any]] = []
    rejected: Counter[str] = Counter()
    seen_urls: set[str] = set()
    aliases = company.get("aliases", [])

    for candidate in candidates:
        title = str(candidate.get("title") or "").strip()
        url = canonical_url(str(candidate.get("url") or ""))
        published = parse_provider_datetime(
            str(candidate.get("seendate") or candidate.get("publishedAt") or "")
        )
        if not title or not url:
            rejected["missing_required_fields"] += 1
            continue
        if published is None or not (start_utc <= published < end_utc):
            rejected["outside_day"] += 1
            continue
        if url in seen_urls:
            rejected["duplicate_url"] += 1
            continue

        description = str(candidate.get("description") or "").strip()
        text = f"{title}. {description}".strip()
        has_alias = alias_in_text(aliases, text)
        specific_alias = alias_in_text(
            [alias for alias in aliases if len(TOKEN_RE.findall(alias.casefold())) >= 2],
            text,
        )
        context_match = alias_in_text(company.get("contextTerms", []), text)
        if company.get("requireHeadlineAlias") and not (specific_alias or (has_alias and context_match)):
            rejected["ambiguous_company_without_alias"] += 1
            continue

        domain = str(candidate.get("domain") or domain_from_url(url)).casefold()
        domain = domain.removeprefix("www.")
        company_domain = str(company.get("domain") or "").casefold().removeprefix("www.")
        first_party = bool(
            domain
            and company_domain
            and (domain == company_domain or domain.endswith(f".{company_domain}"))
        )
        accepted.append(
            {
                "id": "",
                "title": title,
                "description": description,
                "url": url,
                "domain": domain or "unknown",
                "publishedAt": published.isoformat().replace("+00:00", "Z"),
                "language": str(candidate.get("language") or "English"),
                "relevance": 1.0 if has_alias else 0.65,
                "firstParty": first_party,
            }
        )
        accepted[-1]["id"] = article_id(accepted[-1])
        seen_urls.add(url)

    return accepted, rejected


def cluster_articles(
    articles: list[dict[str, Any]], threshold: float
) -> list[list[dict[str, Any]]]:
    clusters: list[list[dict[str, Any]]] = []
    cluster_tokens: list[set[str]] = []
    for article in sorted(articles, key=lambda item: (item["publishedAt"], item["title"])):
        tokens = headline_tokens(article["title"])
        best_index = -1
        best_similarity = 0.0
        for index, existing_tokens in enumerate(cluster_tokens):
            similarity = jaccard(tokens, existing_tokens)
            if similarity > best_similarity:
                best_similarity = similarity
                best_index = index
        if best_index >= 0 and best_similarity >= threshold:
            clusters[best_index].append(article)
            cluster_tokens[best_index] |= tokens
        else:
            clusters.append([article])
            cluster_tokens.append(set(tokens))
    return clusters


def add_sentiment(articles: list[dict[str, Any]], scorer: SentimentScorer) -> None:
    texts = [
        f"{article['title']}. {article['description']}".strip(". ")
        for article in articles
    ]
    for article, probabilities in zip(articles, scorer.score(texts), strict=True):
        positive = float(probabilities.get("positive", 0.0))
        neutral = float(probabilities.get("neutral", 0.0))
        negative = float(probabilities.get("negative", 0.0))
        polarity = positive - negative
        article["probabilities"] = {
            "positive": round(positive, 5),
            "neutral": round(neutral, 5),
            "negative": round(negative, 5),
        }
        article["polarity"] = round(polarity, 5)
        article["sentimentLabel"] = max(
            ("positive", "neutral", "negative"),
            key=lambda key: probabilities.get(key, 0.0),
        )


def cluster_summaries(clusters: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    summaries = []
    for index, cluster in enumerate(clusters, start=1):
        polarities = [float(article["polarity"]) for article in cluster]
        representative = max(
            cluster,
            key=lambda item: (abs(float(item["polarity"])), item["relevance"], item["publishedAt"]),
        )
        summaries.append(
            {
                "id": f"story-{index:03d}",
                "polarity": statistics.fmean(polarities),
                "relevance": statistics.fmean(float(item["relevance"]) for item in cluster),
                "domains": sorted({item["domain"] for item in cluster}),
                "primaryDomain": representative["domain"],
                "articles": cluster,
                "representative": representative,
                "weight": 1.0,
            }
        )
    return summaries


def apply_publisher_cap(stories: list[dict[str, Any]], cap: float) -> None:
    domains = {story["primaryDomain"] for story in stories}
    if len(domains) < 3:
        return
    for _ in range(20):
        total = sum(float(story["weight"]) for story in stories)
        if total <= 0:
            return
        weights_by_domain: dict[str, float] = defaultdict(float)
        for story in stories:
            weights_by_domain[story["primaryDomain"]] += float(story["weight"])
        changed = False
        for domain, domain_weight in weights_by_domain.items():
            allowed = cap * total
            if domain_weight > allowed + 1e-9:
                scale = allowed / domain_weight
                for story in stories:
                    if story["primaryDomain"] == domain:
                        story["weight"] *= scale
                changed = True
        if not changed:
            break


def score_label(score: float | None) -> str:
    if score is None:
        return "No coverage"
    if score < 25:
        return "Very negative"
    if score < 40:
        return "Negative"
    if score < 45:
        return "Slightly negative"
    if score <= 55:
        return "Neutral / mixed"
    if score <= 60:
        return "Slightly positive"
    if score <= 75:
        return "Positive"
    return "Very positive"


def confidence_band(value: float) -> str:
    if value < 40:
        return "low"
    if value < 70:
        return "medium"
    return "high"


def representative_stories(
    stories: list[dict[str, Any]], maximum: int
) -> list[dict[str, Any]]:
    ordered = sorted(
        stories,
        key=lambda item: (
            abs(float(item["polarity"])) * float(item["weight"]),
            len(item["domains"]),
        ),
        reverse=True,
    )
    output = []
    for story in ordered[:maximum]:
        article = story["representative"]
        output.append(
            {
                "id": article["id"],
                "storyClusterId": story["id"],
                "title": article["title"],
                "url": article["url"],
                "source": article["domain"],
                "publishedAt": article["publishedAt"],
                "polarity": round(float(story["polarity"]), 4),
                "sentiment": score_label(50 + 50 * float(story["polarity"])),
                "publisherCount": len(story["domains"]),
            }
        )
    return output


def load_event_rules(path: Path = EVENT_RULES_PATH) -> list[dict[str, Any]]:
    payload = read_json(path, {"rules": []})
    rules = []
    for item in payload.get("rules", []):
        copy = dict(item)
        copy["compiledPatterns"] = [re.compile(value, re.IGNORECASE) for value in item["patterns"]]
        rules.append(copy)
    return rules


def event_flags(
    articles: list[dict[str, Any]], rules: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    flags = []
    for rule in rules:
        evidence = []
        domains: set[str] = set()
        first_party = False
        for article in articles:
            text = f"{article['title']}. {article['description']}"
            if any(pattern.search(text) for pattern in rule["compiledPatterns"]):
                evidence.append(article["id"])
                domains.add(article["domain"])
                first_party = first_party or bool(article["firstParty"])
        if evidence:
            flags.append(
                {
                    "type": rule["type"],
                    "direction": rule["direction"],
                    "severity": rule["severity"],
                    "status": "confirmed" if len(domains) >= 2 or first_party else "possible",
                    "evidenceArticleIds": evidence[:10],
                    "sourceCount": len(domains),
                }
            )
    return flags


def previous_scored(observations: list[dict[str, Any]]) -> dict[str, Any] | None:
    for observation in reversed(observations):
        if observation.get("dailyScore") is not None:
            return observation
    return None


def rolling_score(
    day: date,
    daily_polarity: float | None,
    confidence: float,
    observations: list[dict[str, Any]],
    half_life_days: float,
) -> float:
    previous = observations[-1] if observations else None
    if previous:
        previous_day = date.fromisoformat(previous["date"])
        elapsed = max(1, (day - previous_day).days)
        previous_polarity = (float(previous.get("rollingScore", 50.0)) - 50.0) / 50.0
        decayed = previous_polarity * (0.5 ** (elapsed / half_life_days))
    else:
        decayed = 0.0
    if daily_polarity is None:
        result = decayed
    else:
        base_alpha = 1 - 0.5 ** (1 / 3)
        alpha = base_alpha * (0.5 + 0.5 * confidence / 100.0)
        result = decayed * (1 - alpha) + daily_polarity * alpha
    return round(max(0.0, min(100.0, 50 + 50 * result)), 1)


def narrative_flags(
    observation: dict[str, Any],
    history: list[dict[str, Any]],
    thresholds: dict[str, Any],
) -> list[dict[str, Any]]:
    flags = []
    score = observation.get("dailyScore")
    confidence = float(observation.get("confidence", 0.0))
    story_count = int(observation.get("storyCount", 0))
    source_count = int(observation.get("sourceCount", 0))
    gates = (
        score is not None
        and confidence >= float(thresholds["minimumConfidence"])
        and story_count >= int(thresholds["minimumStoryCount"])
        and source_count >= int(thresholds["minimumSourceCount"])
    )
    previous = previous_scored(history)
    change = None if score is None or not previous else round(score - float(previous["dailyScore"]), 1)
    observation["changeFromPreviousScoredDay"] = change

    def add(flag_type: str, direction: str, severity: str, detail: str) -> None:
        flags.append(
            {
                "type": flag_type,
                "direction": direction,
                "severity": severity,
                "status": "calculated",
                "detail": detail,
                "evidenceArticleIds": [item["id"] for item in observation.get("topStories", [])],
            }
        )

    if gates and change is not None and change >= float(thresholds["sentimentJumpPoints"]):
        add("sentiment_jump", "positive", "high", f"Daily score increased by {change} points")
    if gates and change is not None and change <= float(thresholds["sentimentDropPoints"]):
        add("sentiment_drop", "negative", "high", f"Daily score decreased by {abs(change)} points")
    if gates and score >= float(thresholds["goodPressScore"]):
        add("good_press", "positive", "medium", f"Daily press score is {score}")
    if gates and score <= float(thresholds["badPressScore"]):
        add("bad_press", "negative", "medium", f"Daily press score is {score}")

    trailing_counts = [
        int(item.get("storyCount", 0))
        for item in history[-20:]
        if item.get("coverageStatus") == "ok"
    ]
    if trailing_counts:
        median = statistics.median(trailing_counts)
        minimum = int(thresholds["coverageSpikeMinimumStories"])
        multiple = float(thresholds["coverageSpikeMultiple"])
        if story_count >= minimum and story_count >= max(1.0, median) * multiple:
            add("coverage_spike", "neutral", "medium", f"{story_count} stories versus trailing median {median:g}")
    if (
        observation.get("positiveStoryCount", 0) > 0
        and observation.get("negativeStoryCount", 0) > 0
        and float(observation.get("dispersion", 0.0)) >= float(thresholds["highDisagreementDispersion"])
    ):
        add("high_disagreement", "mixed", "medium", "Positive and negative coverage is unusually dispersed")
    if score is not None and confidence < 40:
        add("low_confidence", "neutral", "low", f"Confidence is {confidence}")
    if observation.get("coverageStatus") == "no_coverage":
        add("no_coverage", "neutral", "low", "Collection succeeded but found no eligible coverage")
    return flags


def score_company_day(
    company: dict[str, Any],
    day: date,
    candidates: list[dict[str, Any]],
    scorer: SentimentScorer,
    methodology: dict[str, Any],
    rules: list[dict[str, Any]],
    history: list[dict[str, Any]],
    collection_completeness: float = 1.0,
) -> dict[str, Any]:
    start_utc, end_utc = day_bounds(day)
    articles, rejected = prepare_articles(candidates, company, start_utc, end_utc)
    add_sentiment(articles, scorer)

    scoring_config = methodology["scoring"]
    score_articles = [article for article in articles if not article["firstParty"]]
    clusters = cluster_articles(score_articles, float(scoring_config["headlineSimilarityThreshold"]))
    stories = cluster_summaries(clusters)
    apply_publisher_cap(stories, float(scoring_config["publisherWeightCap"]))

    total_weight = sum(float(story["weight"]) for story in stories)
    if total_weight:
        polarity_sum = sum(float(story["polarity"]) * float(story["weight"]) for story in stories)
        prior = float(scoring_config["neutralPriorStoryWeight"])
        daily_polarity = polarity_sum / (total_weight + prior)
        daily_score = round(50 + 50 * daily_polarity, 1)
    else:
        daily_polarity = None
        daily_score = None

    source_count = len({domain for story in stories for domain in story["domains"]})
    mean_relevance = (
        statistics.fmean(float(story["relevance"]) for story in stories) if stories else 0.0
    )
    decisive_share = (
        statistics.fmean(1.0 - float(article["probabilities"]["neutral"]) for article in score_articles)
        if score_articles
        else 0.0
    )
    coverage_factor = 1 - math.exp(-total_weight / 3) if total_weight else 0.0
    diversity_factor = min(1.0, source_count / 3)
    confidence = round(
        100
        * coverage_factor
        * (0.55 + 0.45 * diversity_factor)
        * mean_relevance
        * (0.75 + 0.25 * decisive_share)
        * collection_completeness,
        1,
    )
    polarities = [float(story["polarity"]) for story in stories]
    dispersion = round(statistics.pstdev(polarities), 4) if len(polarities) > 1 else 0.0
    top_stories = representative_stories(
        stories, int(methodology["collection"]["maxStoredStoriesPerDay"])
    )
    observation = {
        "date": day.isoformat(),
        "dailyScore": daily_score,
        "dailyLabel": score_label(daily_score),
        "rollingScore": rolling_score(
            day,
            daily_polarity,
            confidence,
            history,
            float(scoring_config["rollingHalfLifeDays"]),
        ),
        "confidence": confidence,
        "confidenceBand": confidence_band(confidence),
        "coverageStatus": "ok" if stories else "no_coverage",
        "collectionCompleteness": round(collection_completeness, 2),
        "candidateCount": len(candidates),
        "eligibleArticleCount": len(score_articles),
        "storyCount": len(stories),
        "effectiveStoryCount": round(total_weight, 3),
        "sourceCount": source_count,
        "positiveStoryCount": sum(1 for item in polarities if item > 0.15),
        "neutralStoryCount": sum(1 for item in polarities if -0.15 <= item <= 0.15),
        "negativeStoryCount": sum(1 for item in polarities if item < -0.15),
        "dispersion": dispersion,
        "rejectedCandidateCounts": dict(sorted(rejected.items())),
        "topStories": top_stories,
        "flags": [],
    }
    observation["flags"] = event_flags(articles, rules)
    observation["flags"].extend(
        narrative_flags(observation, history, methodology["flagThresholds"])
    )
    if collection_completeness < 1.0:
        observation["flags"].append(
            {
                "type": "collection_degraded",
                "direction": "neutral",
                "severity": "medium",
                "status": "calculated",
                "detail": "The provider result ceiling was reached for this day",
                "evidenceArticleIds": [item["id"] for item in top_stories],
            }
        )
    return observation


def replace_observation(
    history: list[dict[str, Any]], observation: dict[str, Any], retention: int
) -> list[dict[str, Any]]:
    filtered = [item for item in history if item.get("date") != observation["date"]]
    filtered.append(observation)
    filtered.sort(key=lambda item: item["date"])
    return filtered[-retention:]


def summary_from_observation(company: dict[str, Any], observation: dict[str, Any]) -> dict[str, Any]:
    return {
        "companyName": company["companyName"],
        "ticker": company["ticker"],
        "slug": company["slug"],
        "date": observation["date"],
        "dailyScore": observation["dailyScore"],
        "dailyLabel": observation["dailyLabel"],
        "rollingScore": observation["rollingScore"],
        "confidence": observation["confidence"],
        "confidenceBand": observation["confidenceBand"],
        "coverageStatus": observation["coverageStatus"],
        "storyCount": observation["storyCount"],
        "sourceCount": observation["sourceCount"],
        "changeFromPreviousScoredDay": observation.get("changeFromPreviousScoredDay"),
        "flags": observation["flags"],
        "topStories": observation["topStories"],
    }


def load_company_history(company: dict[str, Any], methodology_version: str) -> dict[str, Any]:
    path = HISTORY_DIR / f"{company['slug']}.json"
    return read_json(
        path,
        {
            "schemaVersion": 1,
            "methodologyVersion": methodology_version,
            "companyName": company["companyName"],
            "ticker": company["ticker"],
            "slug": company["slug"],
            "observations": [],
        },
    )


def partition_candidates_by_day(
    candidates: list[dict[str, Any]], start_day: date, end_day: date
) -> dict[date, list[dict[str, Any]]]:
    partitioned = {
        start_day + timedelta(days=offset): []
        for offset in range((end_day - start_day).days)
    }
    for candidate in candidates:
        published = parse_provider_datetime(
            str(candidate.get("seendate") or candidate.get("publishedAt") or "")
        )
        if published is None:
            continue
        london_day = published.astimezone(LONDON).date()
        if london_day in partitioned:
            partitioned[london_day].append(candidate)
    return partitioned


def merge_partitions(
    target: dict[date, list[dict[str, Any]]],
    source: dict[date, list[dict[str, Any]]],
) -> None:
    for day, candidates in source.items():
        target.setdefault(day, []).extend(candidates)


def fetch_window_adaptive(
    provider: GdeltProvider,
    company: dict[str, Any],
    start_day: date,
    end_day: date,
    max_records: int,
) -> tuple[dict[date, list[dict[str, Any]]], set[date], int]:
    """Fetch [start_day, end_day), splitting result-capped windows recursively."""
    start_utc = day_bounds(start_day)[0]
    end_utc = day_bounds(end_day)[0]
    candidates = provider.fetch(company, start_utc, end_utc)
    request_count = 1
    span_days = (end_day - start_day).days
    if len(candidates) >= max_records and span_days > 1:
        midpoint = start_day + timedelta(days=max(1, span_days // 2))
        left, left_truncated, left_requests = fetch_window_adaptive(
            provider, company, start_day, midpoint, max_records
        )
        right, right_truncated, right_requests = fetch_window_adaptive(
            provider, company, midpoint, end_day, max_records
        )
        merge_partitions(left, right)
        return (
            left,
            left_truncated | right_truncated,
            request_count + left_requests + right_requests,
        )
    truncated = {start_day} if len(candidates) >= max_records and span_days == 1 else set()
    return partition_candidates_by_day(candidates, start_day, end_day), truncated, request_count


def processing_days(args: argparse.Namespace) -> list[date]:
    if args.date and args.backfill_days:
        raise ValueError("Use --date or --backfill-days, not both")
    end_day = date.fromisoformat(args.date) if args.date else datetime.now(LONDON).date() - timedelta(days=1)
    count = int(args.backfill_days or 1)
    if count < 1 or count > 90:
        raise ValueError("--backfill-days must be between 1 and 90")
    start_day = end_day - timedelta(days=count - 1)
    return [start_day + timedelta(days=offset) for offset in range(count)]


def missing_day_windows(
    days: list[date], observations: list[dict[str, Any]], max_window_days: int
) -> tuple[list[date], list[tuple[date, date]]]:
    """Return missing requested days and contiguous half-open fetch windows."""
    existing = {item.get("date") for item in observations}
    missing = [day for day in days if day.isoformat() not in existing]
    windows: list[tuple[date, date]] = []
    if not missing:
        return missing, windows
    start = missing[0]
    previous = start
    for current in missing[1:]:
        window_full = (current - start).days >= max_window_days
        if current != previous + timedelta(days=1) or window_full:
            windows.append((start, previous + timedelta(days=1)))
            start = current
        previous = current
    windows.append((start, previous + timedelta(days=1)))
    return missing, windows


def run(args: argparse.Namespace) -> int:
    methodology = read_json(METHODOLOGY_PATH, None)
    universe = read_json(args.universe, None)
    if not methodology or not universe:
        raise RuntimeError("Methodology and company universe are required")
    companies = universe.get("companies", [])
    requested = {item.strip() for item in (args.slugs or "").split(",") if item.strip()}
    if requested:
        known = {company["slug"] for company in companies}
        unknown = requested - known
        if unknown:
            raise ValueError(f"Unknown company slugs: {', '.join(sorted(unknown))}")
        companies = [company for company in companies if company["slug"] in requested]
    if not companies:
        raise ValueError("No companies selected")

    days = processing_days(args)
    is_backfill = len(days) > 1
    max_records = int(
        methodology["collection"].get("backfillMaxCandidatesPerWindow", 250)
        if is_backfill
        else methodology["collection"]["maxCandidatesPerCompany"]
    )
    provider = None
    scorer: SentimentScorer | None = None
    if not args.rebuild_only:
        provider = GdeltProvider(
            max_records=max_records,
            request_delay=(
                args.request_delay
                if args.request_delay is not None
                else float(methodology["collection"].get("minimumSecondsBetweenRequests", 5.25))
            ),
        )
        scorer = (
            KeywordTestScorer()
            if args.test_scorer
            else FinBertScorer(
                methodology["model"]["id"], methodology["model"].get("revision", "main")
            )
        )
    rules = load_event_rules()
    latest = read_json(
        LATEST_PATH,
        {
            "schemaVersion": 1,
            "methodologyVersion": methodology["methodologyVersion"],
            "generatedAt": None,
            "asOfDate": None,
            "companies": {},
        },
    )
    statuses = []
    completed = 0
    total_targets = len(companies) * len(days)

    for index, company in enumerate(companies, start=1):
        slug = company["slug"]
        print(f"[{index}/{len(companies)}] {company['companyName']} ({slug})", flush=True)
        history_payload = load_company_history(company, methodology["methodologyVersion"])
        observations = history_payload.get("observations", [])
        window_days = (
            int(methodology["collection"].get("backfillWindowDays", 5))
            if is_backfill
            else 1
        )
        missing_days, fetch_windows = missing_day_windows(days, observations, window_days)
        candidates_by_day: dict[date, list[dict[str, Any]]] = {
            day: [] for day in missing_days
        }
        truncated_days: set[date] = set()
        failed_days: dict[date, str] = {}
        requests_before = provider.request_count if provider is not None else 0
        if not args.rebuild_only:
            assert provider is not None and scorer is not None
            for window_start, window_end in fetch_windows:
                try:
                    partitioned, truncated, _ = fetch_window_adaptive(
                        provider, company, window_start, window_end, max_records
                    )
                    merge_partitions(candidates_by_day, partitioned)
                    truncated_days |= truncated
                except Exception as exc:  # continue with other windows and companies
                    message = str(exc)[:500]
                    print(
                        f"ERROR {slug} {window_start}..{window_end - timedelta(days=1)}: {message}",
                        file=sys.stderr,
                        flush=True,
                    )
                    current = window_start
                    while current < window_end:
                        failed_days[current] = message
                        current += timedelta(days=1)

            for processing_day in missing_days:
                if processing_day in failed_days:
                    continue
                prior_observations = [
                    item for item in observations if item.get("date", "") < processing_day.isoformat()
                ]
                observation = score_company_day(
                    company,
                    processing_day,
                    candidates_by_day.get(processing_day, []),
                    scorer,
                    methodology,
                    rules,
                    prior_observations,
                    collection_completeness=0.75 if processing_day in truncated_days else 1.0,
                )
                observations = replace_observation(
                    observations,
                    observation,
                    int(methodology["scoring"]["historyRetentionDays"]),
                )
        requested_date_strings = {item.isoformat() for item in days}
        requested_observations = [
            item for item in observations if item.get("date") in requested_date_strings
        ]
        completed_dates = {item.get("date") for item in requested_observations}
        company_completed = len(completed_dates)
        completed += company_completed
        coverage_days = sum(
            1 for item in requested_observations if item.get("coverageStatus") == "ok"
        )
        latest_observation = max(
            requested_observations, key=lambda item: item.get("date", ""), default=None
        )
        missing_after_run = [
            item for item in days if item.isoformat() not in completed_dates
        ]
        observed_truncated_days = {
            date.fromisoformat(item["date"])
            for item in requested_observations
            if float(item.get("collectionCompleteness", 1.0)) < 1.0
        }
        truncated_days |= observed_truncated_days

        history_payload["methodologyVersion"] = methodology["methodologyVersion"]
        history_payload["observations"] = observations
        if latest_observation is not None:
            latest["companies"][slug] = summary_from_observation(company, latest_observation)
            if not args.dry_run:
                write_json_atomic(HISTORY_DIR / f"{slug}.json", history_payload)
        statuses.append(
            {
                "slug": slug,
                "status": "ok" if not missing_after_run and not truncated_days else "degraded",
                "requestedDayCount": len(days),
                "completedDayCount": company_completed,
                "coverageDayCount": coverage_days,
                "failedDates": [item.isoformat() for item in missing_after_run],
                "truncatedDates": [item.isoformat() for item in sorted(truncated_days)],
                "requestCount": (
                    provider.request_count - requests_before if provider is not None else 0
                ),
                "error": next(iter(failed_days.values()), None),
            }
        )

    generated_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    completeness = completed / total_targets
    latest["methodologyVersion"] = methodology["methodologyVersion"]
    latest["generatedAt"] = generated_at
    latest["asOfDate"] = days[-1].isoformat()
    run_status = {
        "schemaVersion": 1,
        "methodologyVersion": methodology["methodologyVersion"],
        "generatedAt": generated_at,
        "date": days[-1].isoformat() if len(days) == 1 else None,
        "dateRange": {"start": days[0].isoformat(), "end": days[-1].isoformat()},
        "provider": (
            provider.name if provider is not None else methodology["collection"]["provider"]
        ),
        "model": scorer.model_id if scorer is not None else methodology["model"]["id"],
        "selectedCompanyCount": len(companies),
        "requestedDayCount": len(days),
        "requestedObservationCount": total_targets,
        "completedObservationCount": completed,
        "completeness": round(completeness, 4),
        "minimumCompleteness": args.minimum_completeness,
        "status": "ok" if completeness >= args.minimum_completeness else "degraded",
        "companies": statuses,
    }
    if args.dry_run:
        print(json.dumps(run_status, indent=2))
    else:
        write_json_atomic(LATEST_PATH, latest)
        write_json_atomic(RUN_STATUS_PATH, run_status)
    return 0 if completeness >= args.minimum_completeness else 2


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", help="London calendar date (YYYY-MM-DD); default is previous day")
    parser.add_argument("--backfill-days", type=int, help="Backfill N completed London days ending yesterday")
    parser.add_argument("--slugs", help="Optional comma-separated company slugs")
    parser.add_argument("--universe", type=Path, default=UNIVERSE_PATH)
    parser.add_argument("--minimum-completeness", type=float, default=0.9)
    parser.add_argument("--request-delay", type=float)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--rebuild-only",
        action="store_true",
        help="Rebuild latest and run status from stored history without provider requests",
    )
    parser.add_argument("--test-scorer", action="store_true", help=argparse.SUPPRESS)
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(run(parse_args()))

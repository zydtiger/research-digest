"""Fetch candidate items and attention signals from public sources."""

from __future__ import annotations

import os
import re
import time
from collections.abc import Callable, Iterable
from datetime import UTC, date, datetime, timedelta
from time import struct_time
from typing import Any
from xml.etree.ElementTree import Element, ParseError

import feedparser
import httpx
from defusedxml import ElementTree

from .config import Config
from .items import Item

ARXIV_DELAY_SECONDS = 3.0
ARXIV_ATTEMPTS = 5
ARXIV_PAGE_SIZE = 500
ARXIV_RETRY_SECONDS = 30.0
ARXIV_API_URL = "https://export.arxiv.org/api/query"
ARXIV_ID = re.compile(r"arxiv\.org/(?:abs|pdf|html)/(\d{4}\.\d{4,5})")
GITHUB_SEARCH_URL = "https://api.github.com/search/repositories"
GITHUB_LIMIT_WAIT_SECONDS = 65.0
USER_AGENT = "research-digest/0.1"

ATOM = "{http://www.w3.org/2005/Atom}"
OPENSEARCH = "{http://a9.com/-/spec/opensearch/1.1/}"


def client() -> httpx.Client:
    return httpx.Client(
        headers={"User-Agent": USER_AGENT},
        follow_redirects=True,
        timeout=httpx.Timeout(300.0, connect=30.0),
    )


def collect(config: Config, since: date, until: date) -> tuple[list[Item], dict[str, str]]:
    """Run every source, returning its items and a reason for each source that failed."""
    window_start = until - timedelta(days=config.sources.attention_window_days)
    sources = config.sources
    gaps: dict[str, str] = {}
    fetchers: dict[str, Callable[[httpx.Client], list[Item]]] = {
        "arxiv": lambda http: arxiv_records(http, config, since, until, gaps),
        "preprints": lambda http: preprints(http, config, since, until),
        "hf_daily": lambda http: hf_daily(http, window_start, until),
        "hf_models": lambda http: hf_models(http, sources.hf_model_limit, window_start, until),
        "hacker_news": lambda http: hacker_news(http, sources.hn_min_points, window_start, until),
        "github": lambda http: github(http, sources.github_topics, sources.github_limit, since),
        "blogs": lambda http: blogs(http, sources.blogs, since, until),
    }

    items: list[Item] = []
    with client() as http:
        for name, fetch in fetchers.items():
            try:
                fetched = fetch(http)
            except (httpx.HTTPError, ValueError, KeyError, TypeError) as error:
                gaps[name] = f"{type(error).__name__}: {error}"
                continue
            print(f"collect: {name}: {len(fetched)} items", flush=True)
            items.extend(fetched)
        try:
            items.extend(arxiv_lookup(http, _uncategorized_papers(items)))
        except (httpx.HTTPError, ValueError) as error:
            gaps["arxiv categories"] = f"{type(error).__name__}: {error}"
    return items, gaps


def _uncategorized_papers(items: Iterable[Item]) -> list[str]:
    """Return arXiv identifiers known only from sources that do not give categories."""
    papers = [item for item in items if item.id.startswith("arxiv:")]
    categorized = {item.id for item in papers if item.categories}
    return sorted(
        {item.id.removeprefix("arxiv:") for item in papers}
        - {identifier.removeprefix("arxiv:") for identifier in categorized}
    )


def arxiv_records(
    http: httpx.Client,
    config: Config,
    since: date,
    until: date,
    gaps: dict[str, str],
) -> list[Item]:
    """Query each category, recording a failed one without losing the others."""
    categories = sorted(
        {category for field in config.fields for category in field.arxiv_categories}
    )
    submitted_after = since - timedelta(days=config.sources.arrival_grace_days)
    items: dict[str, Item] = {}
    for category in categories:
        try:
            _query_category(http, category, submitted_after, until, items)
        except (httpx.HTTPError, ValueError) as error:
            gaps[f"arxiv {category}"] = f"{type(error).__name__}: {error}"
    return list(items.values())


def _query_category(
    http: httpx.Client,
    category: str,
    submitted_after: date,
    until: date,
    items: dict[str, Item],
) -> None:
    window = f"[{submitted_after:%Y%m%d}0000 TO {until:%Y%m%d}2359]"
    params = {
        "search_query": f"cat:{category} AND submittedDate:{window}",
        "sortBy": "submittedDate",
        "sortOrder": "ascending",
        "max_results": str(ARXIV_PAGE_SIZE),
    }
    start = 0
    while True:
        entries, total = _arxiv_page(http, {**params, "start": str(start)})
        for entry in entries:
            item = parse_atom_entry(entry)
            items[item.id] = item
        start += len(entries)
        time.sleep(ARXIV_DELAY_SECONDS)
        if start >= total:
            return


def _arxiv_page(http: httpx.Client, params: dict[str, str]) -> tuple[list[Element], int]:
    """Fetch one page, retrying server errors and the API's occasional empty pages."""
    failure = "no attempts were made"
    for _ in range(ARXIV_ATTEMPTS):
        try:
            response = http.get(ARXIV_API_URL, params=params)
            response.raise_for_status()
            root = ElementTree.fromstring(response.content)
        except (httpx.HTTPError, ParseError) as error:
            failure = f"{type(error).__name__}: {error}"
        else:
            entries = root.findall(f"{ATOM}entry")
            total = int(_text(root, f"{OPENSEARCH}totalResults") or 0)
            if entries or int(params["start"]) >= total:
                return entries, total
            failure = f"empty page at {params['start']} of {total}"
        time.sleep(ARXIV_RETRY_SECONDS)
    raise ValueError(f"arXiv query failed: {failure}")


def arxiv_lookup(http: httpx.Client, arxiv_ids: list[str]) -> list[Item]:
    """Fetch metadata for papers known only from a link."""
    items: list[Item] = []
    for start in range(0, len(arxiv_ids), 50):
        batch = arxiv_ids[start : start + 50]
        entries, _ = _arxiv_page(
            http,
            {"id_list": ",".join(batch), "max_results": str(len(batch)), "start": "0"},
        )
        items.extend(parse_atom_entry(entry) for entry in entries)
        time.sleep(ARXIV_DELAY_SECONDS)
    return items


def parse_atom_entry(entry: Element) -> Item:
    match = ARXIV_ID.search(_text(entry, f"{ATOM}id"))
    if match is None:
        raise ValueError("arXiv entry has no identifier")
    arxiv_id = match.group(1)
    return Item(
        id=f"arxiv:{arxiv_id}",
        kind="paper",
        title=_clean(_text(entry, f"{ATOM}title")),
        abstract=_clean(_text(entry, f"{ATOM}summary")),
        authors=[_text(author, f"{ATOM}name") for author in entry.iterfind(f"{ATOM}author")],
        categories=[
            category.get("term", "")
            for category in entry.iterfind(f"{ATOM}category")
            if category.get("term")
        ],
        published=_text(entry, f"{ATOM}published")[:10],
        url=f"https://arxiv.org/abs/{arxiv_id}",
        sources=["arxiv"],
    )


def preprints(http: httpx.Client, config: Config, since: date, until: date) -> list[Item]:
    sources = config.sources
    items: list[Item] = []
    categories = {category.lower() for category in sources.preprint_categories}
    keywords = [keyword.lower() for keyword in sources.preprint_keywords]
    for server in sources.preprint_servers:
        cursor = 0
        while True:
            response = http.get(
                f"https://api.biorxiv.org/details/{server}/{since}/{until}/{cursor}"
            )
            response.raise_for_status()
            data = response.json()
            collection = data.get("collection", [])
            for record in collection:
                item = parse_preprint(server, record)
                text = f"{item.title} {item.abstract}".lower()
                if (
                    record.get("version") == "1"
                    and record.get("category", "").lower() in categories
                    and any(keyword in text for keyword in keywords)
                ):
                    items.append(item)
            cursor += len(collection)
            if not collection or cursor >= int(data["messages"][0].get("total", 0)):
                break
    return items


def parse_preprint(server: str, record: dict[str, Any]) -> Item:
    doi = record["doi"]
    return Item(
        id=f"doi:{doi}",
        kind="paper",
        title=_clean(record["title"]),
        abstract=_clean(record.get("abstract", "")),
        authors=[name.strip() for name in record.get("authors", "").split(";") if name.strip()],
        categories=[record.get("category", "")],
        published=record["date"],
        url=f"https://www.{server}.org/content/{doi}v{record.get('version', '1')}",
        sources=[server],
    )


def hf_daily(http: httpx.Client, start: date, until: date) -> list[Item]:
    items: list[Item] = []
    day = start
    while day <= until:
        response = http.get(
            "https://huggingface.co/api/daily_papers",
            params={"date": day.isoformat(), "limit": "100"},
        )
        response.raise_for_status()
        parsed = (parse_hf_daily(entry) for entry in response.json())
        items.extend(item for item in parsed if item.published)
        day += timedelta(days=1)
    return items


def parse_hf_daily(entry: dict[str, Any]) -> Item:
    paper = entry["paper"]
    arxiv_id = paper["id"]
    signals: dict[str, float] = {
        "hf_upvotes": float(paper.get("upvotes") or 0),
        "hf_comments": float(entry.get("numComments") or 0),
    }
    if paper.get("githubStars"):
        signals["github_stars"] = float(paper["githubStars"])
    return Item(
        id=f"arxiv:{arxiv_id}",
        kind="paper",
        title=_clean(paper["title"]),
        abstract=_clean(paper.get("summary", "")),
        authors=[author["name"] for author in paper.get("authors", []) if author.get("name")],
        published=(paper.get("publishedAt") or entry.get("publishedAt", ""))[:10],
        url=f"https://arxiv.org/abs/{arxiv_id}",
        repo=_repo_name(paper.get("githubRepo")),
        sources=["hf_daily"],
        signals=signals,
    )


def hf_models(http: httpx.Client, limit: int, start: date, until: date) -> list[Item]:
    response = http.get(
        "https://huggingface.co/api/models",
        params={"sort": "trendingScore", "limit": str(limit), "full": "true"},
    )
    response.raise_for_status()
    items = []
    for entry in response.json():
        item = parse_hf_model(entry)
        if item is not None and start.isoformat() <= item.published <= until.isoformat():
            item.abstract = _model_card(http, item.id.removeprefix("hf:")) or item.abstract
            items.append(item)
    return items


def parse_hf_model(entry: dict[str, Any]) -> Item | None:
    """Parse a trending model, skipping quantized repackagings of another model."""
    tags = entry.get("tags", [])
    if "gguf" in tags or any(tag.startswith("base_model:quantized:") for tag in tags):
        return None
    model_id = entry["id"]
    return Item(
        id=f"hf:{model_id}",
        kind="model",
        title=model_id,
        abstract=f"Pipeline: {entry.get('pipeline_tag', 'unknown')}. Tags: {', '.join(tags)}",
        published=(entry.get("createdAt") or "")[:10],
        url=f"https://huggingface.co/{model_id}",
        sources=["hf_models"],
        signals={
            "model_likes": float(entry.get("likes") or 0),
            "model_trending": float(entry.get("trendingScore") or 0),
        },
        arxiv_refs=sorted(
            f"arxiv:{tag.removeprefix('arxiv:')}" for tag in tags if tag.startswith("arxiv:")
        ),
    )


def _model_card(http: httpx.Client, model_id: str) -> str:
    response = http.get(f"https://huggingface.co/{model_id}/raw/main/README.md")
    if response.status_code != 200:
        return ""
    body = re.sub(r"\A---\n.*?\n---\n", "", response.text, flags=re.DOTALL)
    return _clean(body)[:2000]


def hacker_news(http: httpx.Client, min_points: int, start: date, until: date) -> list[Item]:
    """Find arXiv papers discussed on Hacker News, looking up their metadata."""
    begin = int(datetime(start.year, start.month, start.day, tzinfo=UTC).timestamp())
    end = int(datetime(until.year, until.month, until.day, tzinfo=UTC).timestamp()) + 86400
    signals: dict[str, dict[str, float]] = {}
    page = 0
    while True:
        response = http.get(
            "https://hn.algolia.com/api/v1/search_by_date",
            params={
                "query": "arxiv.org",
                "restrictSearchableAttributes": "url",
                "tags": "story",
                "numericFilters": f"created_at_i>={begin},created_at_i<{end},points>={min_points}",
                "hitsPerPage": "100",
                "page": str(page),
            },
        )
        response.raise_for_status()
        data = response.json()
        for hit in data["hits"]:
            match = ARXIV_ID.search(hit.get("url") or "")
            if match is None:
                continue
            current = signals.setdefault(match.group(1), {"hn_points": 0.0, "hn_comments": 0.0})
            current["hn_points"] += float(hit.get("points") or 0)
            current["hn_comments"] += float(hit.get("num_comments") or 0)
        page += 1
        if page >= data.get("nbPages", 0):
            break

    items = arxiv_lookup(http, sorted(signals))
    for item in items:
        item.signals = signals[item.id.removeprefix("arxiv:")]
        item.sources = ["hacker_news"]
    return items


def github(http: httpx.Client, topics: Iterable[str], limit: int, since: date) -> list[Item]:
    headers = {"Accept": "application/vnd.github+json"}
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    items: dict[str, Item] = {}
    for topic in topics:
        params = {
            "q": f"topic:{topic} created:>={since}",
            "sort": "stars",
            "order": "desc",
            "per_page": str(limit),
        }
        response = http.get(GITHUB_SEARCH_URL, params=params, headers=headers)
        if response.status_code in (403, 429):
            # Search allows a few requests per minute, shared with anything else on this address.
            time.sleep(GITHUB_LIMIT_WAIT_SECONDS)
            response = http.get(GITHUB_SEARCH_URL, params=params, headers=headers)
        response.raise_for_status()
        for entry in response.json()["items"]:
            item = parse_github(entry)
            items[item.id] = item
    return list(items.values())


def parse_github(entry: dict[str, Any]) -> Item:
    name = entry["full_name"]
    return Item(
        id=f"gh:{name.lower()}",
        kind="repo",
        title=name,
        abstract=_clean(entry.get("description") or ""),
        published=entry["created_at"][:10],
        url=entry["html_url"],
        repo=name.lower(),
        sources=["github"],
        signals={"github_stars": float(entry.get("stargazers_count") or 0)},
    )


def blogs(http: httpx.Client, urls: Iterable[str], since: date, until: date) -> list[Item]:
    items = []
    failures = []
    for url in urls:
        try:
            response = http.get(url)
            response.raise_for_status()
        except httpx.HTTPError as error:
            failures.append(f"{url}: {error}")
            continue
        items.extend(parse_feed(response.content, url, since, until))
    if failures and not items:
        raise ValueError("; ".join(failures))
    return items


def parse_feed(content: bytes, feed_url: str, since: date, until: date) -> list[Item]:
    parsed: Any = feedparser.parse(content)
    feed_title = parsed.feed.get("title", feed_url)
    items = []
    for entry in parsed.entries:
        published = _entry_date(entry)
        link = entry.get("link")
        if published is None or not link or not since <= published <= until:
            continue
        summary = re.sub(r"<[^>]+>", " ", entry.get("summary", ""))
        items.append(
            Item(
                id=f"url:{link}",
                kind="blog",
                title=_clean(entry.get("title", link)),
                abstract=_clean(summary)[:2000],
                authors=[feed_title],
                published=published.isoformat(),
                url=link,
                sources=["blogs"],
            )
        )
    return items


def _entry_date(entry: Any) -> date | None:
    stamp: struct_time | None = entry.get("published_parsed") or entry.get("updated_parsed")
    return None if stamp is None else date(stamp.tm_year, stamp.tm_mon, stamp.tm_mday)


def _repo_name(url: str | None) -> str | None:
    if not url:
        return None
    match = re.search(r"github\.com/([^/\s]+/[^/\s#?]+)", url)
    return match.group(1).removesuffix(".git").lower() if match else None


def _text(element: Element, path: str) -> str:
    found = element.find(path)
    return "" if found is None or found.text is None else found.text


def _clean(text: str) -> str:
    return " ".join(text.split())

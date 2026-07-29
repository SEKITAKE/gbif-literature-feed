#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
GBIF Literature API の検索結果を RSS 2.0 feed.xml に変換するスクリプト。

目的:
- GBIF Literature API の検索結果に新しい論文が追加されたら RSS Reader で検知できるようにする
- GitHub Actions で 1日1回実行し、GitHub Pages で feed.xml を公開する

出力:
- ./feed.xml
"""

from __future__ import annotations

import email.utils
import html
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
import xml.etree.ElementTree as ET


API_BASE_URL = "https://api.gbif.org/v1/literature/search"

FEED_URL = "https://sekitake.github.io/gbif-literature-feed/feed.xml"
SITE_URL = "https://sekitake.github.io/gbif-literature-feed/"

OUTPUT_FILE = Path("feed.xml")

MAX_ITEMS = 100
API_LIMIT = 100
TIMEOUT_SECONDS = 30

USER_AGENT = "gbif-literature-feed/1.0 (https://github.com/sekitake/gbif-literature-feed)"


def fetch_literature() -> List[Dict[str, Any]]:
    """
    GBIF Literature API から検索結果を取得する。
    エラー時は例外を投げ、GitHub Actions が失敗するようにする。
    """

    params = {
        "literatureType": "JOURNAL",
        "relevance": ["GBIF_USED", "GBIF_CITED"],
        "limit": API_LIMIT,
        "offset": 0,
    }

    query = urllib.parse.urlencode(params, doseq=True)
    url = f"{API_BASE_URL}?{query}"

    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        },
        method="GET",
    )

    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            status = response.status
            body = response.read().decode("utf-8")

            if status < 200 or status >= 300:
                raise RuntimeError(f"GBIF API returned HTTP status {status}: {body[:500]}")

            try:
                data = json.loads(body)
            except json.JSONDecodeError as exc:
                raise RuntimeError("GBIF API response is not valid JSON") from exc

    except urllib.error.HTTPError as exc:
        error_body = ""
        try:
            error_body = exc.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        raise RuntimeError(
            f"GBIF API HTTPError: status={exc.code}, reason={exc.reason}, body={error_body[:500]}"
        ) from exc

    except urllib.error.URLError as exc:
        raise RuntimeError(f"GBIF API connection error: {exc.reason}") from exc

    except TimeoutError as exc:
        raise RuntimeError(f"GBIF API timeout after {TIMEOUT_SECONDS} seconds") from exc

    if not isinstance(data, dict):
        raise RuntimeError("GBIF API response root is not an object")

    results = data.get("results")
    if not isinstance(results, list):
        raise RuntimeError("GBIF API response does not contain a valid 'results' list")

    return results


def first_non_empty(*values: Any) -> str:
    for value in values:
        if value is None:
            continue
        if isinstance(value, str):
            if value.strip():
                return value.strip()
        else:
            text = str(value).strip()
            if text:
                return text
    return ""


def normalize_list(value: Any) -> List"""
    authors や relevance が list / str / dict のどれでも扱えるようにする。
    """
    if value is None:
        return []

    if isinstance(value, list):
        result = []
        for item in value:
            if isinstance(item, str):
                if item.strip():
                    result.append(item.strip())
            elif isinstance(item, dict):
                name = first_non_empty(
                    item.get("name"),
                    item.get("title"),
                    item.get("lastName"),
                    item.get("firstName"),
                )
                if name:
                    result.append(name)
            else:
                text = str(item).strip()
                if text:
                    result.append(text)
        return result

    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []

    if isinstance(value, dict):
        text = first_non_empty(value.get("name"), value.get("title"))
        return [text] if text else []

    text = str(value).strip()
    return [text] if text else []


def extract_doi(item: Dict[str, Any]) -> str:
    direct = first_non_empty(item.get("doi"), item.get("DOI"))
    if direct:
        return direct.replace("https://doi.org/", "").replace("http://doi.org/", "").strip()

    identifiers = item.get("identifiers")
    if isinstance(identifiers, list):
        for identifier in identifiers:
            if isinstance(identifier, str):
                lower = identifier.lower()
                if "doi.org/" in lower:
                    return identifier.split("doi.org/")[-1].strip()
                if lower.startswith("10."):
                    return identifier.strip()

            if isinstance(identifier, dict):
                value = first_non_empty(
                    identifier.get("identifier"),
                    identifier.get("value"),
                    identifier.get("id"),
                )
                scheme = first_non_empty(
                    identifier.get("scheme"),
                    identifier.get("type"),
                ).lower()

                if scheme == "doi" and value:
                    return value.replace("https://doi.org/", "").replace("http://doi.org/", "").strip()

                lower_value = value.lower()
                if "doi.org/" in lower_value:
                    return value.split("doi.org/")[-1].strip()
                if lower_value.startswith("10."):
                    return value.strip()

    return ""


def extract_title(item: Dict[str, Any]) -> str:
    return first_non_empty(
        item.get("title"),
        item.get("name"),
        item.get("citation"),
        "Untitled GBIF literature item",
    )


def extract_key(item: Dict[str, Any]) -> str:
    return first_non_empty(item.get("key"), item.get("id"), item.get("uuid"))


def extract_link(item: Dict[str, Any], key: str, doi: str) -> str:
    if key:
        return f"https://www.gbif.org/literature/{urllib.parse.quote(str(key))}"

    if doi:
        return f"https://doi.org/{urllib.parse.quote(doi)}"

    for field in ("url", "link", "homepage", "website"):
        value = first_non_empty(item.get(field))
        if value.startswith("http://") or value.startswith("https://"):
            return value

    websites = item.get("websites")
    if isinstance(websites, list):
        for website in websites:
            if isinstance(website, str) and website.startswith(("http://", "https://")):
                return website
            if isinstance(website, dict):
                value = first_non_empty(website.get("url"), website.get("link"))
                if value.startswith(("http://", "https://")):
                    return value

    return SITE_URL


def parse_date(value: str) -> Optionalif not value:
        return None

    raw = value.strip()
    normalized = raw.replace("Z", "+00:00")

    candidates = [
        normalized,
        normalized[:10],
        normalized[:7] + "-01" if len(normalized) >= 7 else normalized,
        normalized[:4] + "-01-01" if len(normalized) >= 4 else normalized,
    ]

    for candidate in candidates:
        try:
            dt = datetime.fromisoformat(candidate)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except ValueError:
            continue

    return None


def extract_pub_datetime(item: Dict[str, Any]) -> datetime:
    """
    pubDate は modified 優先。
    なければ publicationDate を使う。
    """
    date_text = first_non_empty(
        item.get("modified"),
        item.get("publicationDate"),
        item.get("published"),
        item.get("date"),
        item.get("created"),
        item.get("createdDate"),
    )

    parsed = parse_date(date_text)
    if parsed is not None:
        return parsed

    return datetime.now(timezone.utc)


def rfc2822_date(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return email.utils.format_datetime(dt.astimezone(timezone.utc), usegmt=True)


def extract_journal(item: Dict[str, Any]) -> str:
    return first_non_empty(
        item.get("journal"),
        item.get("source"),
        item.get("containerTitle"),
        item.get("publisher"),
        item.get("periodical"),
    )


def build_description(item: Dict[str, Any], doi: str, pub_dt: datetime) -> str:
    parts = []

    citation = first_non_empty(item.get("citation"))
    if citation:
        parts.append(f"<p><strong>Citation:</strong> {html.escape(citation)}</p>")

    journal = extract_journal(item)
    if journal:
        parts.append(f"<p><strong>Journal / Source:</strong> {html.escape(journal)}</p>")

    if doi:
        safe_doi = html.escape(doi)
        doi_url = f"https://doi.org/{safe_doi}"
        parts.append(
            f'<p><strong>DOI:</strong> {doi_url}{safe_doi}</a></p>'
        )

    authors = normalize_list(item.get("authors"))
    if authors:
        author_text = ", ".join(authors[:10])
        if len(authors) > 10:
            author_text += " et al."
        parts.append(f"<p><strong>Authors:</strong> {html.escape(author_text)}</p>")

    parts.append(
        f"<p><strong>Publication date:</strong> {html.escape(pub_dt.date().isoformat())}</p>"
    )

    relevance = normalize_list(item.get("relevance"))
    if relevance:
        parts.append(
            f"<p><strong>GBIF relevance:</strong> {html.escape(', '.join(relevance))}</p>"
        )

    abstract = first_non_empty(item.get("abstract"))
    if abstract:
        parts.append(f"<p>{html.escape(abstract)}</p>")

    if not parts:
        parts.append("<p>GBIF literature search result.</p>")

    return "\n".join(parts)


def deduplicate_items(items: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    key を優先して重複除去する。
    key がない場合は DOI、それもなければ title + publicationDate を使う。
    """
    seen = set()
    unique_items = []

    for item in items:
        key = extract_key(item)
        doi = extract_doi(item).lower()
        title = extract_title(item).lower()
        date_text = first_non_empty(item.get("modified"), item.get("publicationDate"))

        if key:
            dedupe_id = f"key:{key}"
        elif doi:
            dedupe_id = f"doi:{doi}"
        else:
            dedupe_id = f"title-date:{title}:{date_text}"

        if dedupe_id in seen:
            continue

        seen.add(dedupe_id)
        unique_items.append(item)

    return unique_items


def sort_items_newest_first(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return sorted(items, key=extract_pub_datetime, reverse=True)


def build_rss(items: List[Dict[str, Any]]) -> ET.ElementTree:
    ET.register_namespace("atom", "http://www.w3.org/2005/Atom")

    rss = ET.Element(
        "rss",
        {
            "version": "2.0",
            "xmlns:atom": "http://www.w3.org/2005/Atom",
        },
    )

    channel = ET.SubElement(rss, "channel")

    ET.SubElement(channel, "title").text = "GBIF Literature: GBIF used or cited journal articles"
    ET.SubElement(channel, "link").text = SITE_URL
    ET.SubElement(channel, "description").text = (
        "RSS feed generated from GBIF Literature API search results for journal articles "
        "where GBIF is used or cited."
    )
    ET.SubElement(channel, "language").text = "en"
    ET.SubElement(channel, "lastBuildDate").text = rfc2822_date(datetime.now(timezone.utc))

    atom_link = ET.SubElement(channel, "{http://www.w3.org/2005/Atom}link")
    atom_link.set("href", FEED_URL)
    atom_link.set("rel", "self")
    atom_link.set("type", "application/rss+xml")

    for item in items[:MAX_ITEMS]:
        key = extract_key(item)
        doi = extract_doi(item)
        title = extract_title(item)
        pub_dt = extract_pub_datetime(item)
        link = extract_link(item, key, doi)
        description = build_description(item, doi, pub_dt)

        rss_item = ET.SubElement(channel, "item")

        ET.SubElement(rss_item, "title").text = title
        ET.SubElement(rss_item, "link").text = link
        ET.SubElement(rss_item, "description").text = description
        ET.SubElement(rss_item, "pubDate").text = rfc2822_date(pub_dt)

        guid = ET.SubElement(rss_item, "guid")
        guid.set("isPermaLink", "false")

        if key:
            guid.text = f"gbif-literature:{key}"
        elif doi:
            guid.text = f"doi:{doi}"
        else:
            guid.text = f"gbif-literature:{title}:{pub_dt.date().isoformat()}"

        journal = extract_journal(item)
        if journal:
            ET.SubElement(rss_item, "category").text = journal

    return ET.ElementTree(rss)


def write_feed(tree: ET.ElementTree) -> None:
    tree.write(
        OUTPUT_FILE,
        encoding="utf-8",
        xml_declaration=True,
        short_empty_elements=True,
    )


def main() -> int:
    try:
        raw_items = fetch_literature()
        unique_items = deduplicate_items(raw_items)
        sorted_items = sort_items_newest_first(unique_items)
        limited_items = sorted_items[:MAX_ITEMS]

        if not limited_items:
            raise RuntimeError("GBIF API returned zero literature items")

        tree = build_rss(limited_items)
        write_feed(tree)

        print(f"Successfully generated {OUTPUT_FILE}")
        print(f"Raw items: {len(raw_items)}")
        print(f"Unique items: {len(unique_items)}")
        print(f"RSS items: {len(limited_items)}")
        return 0

    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

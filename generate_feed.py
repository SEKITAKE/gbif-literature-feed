#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import sys
import html
import urllib.request
import urllib.parse
from datetime import datetime, timezone
from email.utils import format_datetime
import xml.etree.ElementTree as ET


API_URL = "https://api.gbif.org/v1/literature/search"

FEED_URL = "https://sekitake.github.io/gbif-literature-feed/feed.xml"
SITE_URL = "https://sekitake.github.io/gbif-literature-feed/"

OUTPUT_FILE = "feed.xml"

MAX_ITEMS = 100
TIMEOUT_SECONDS = 30


def get_text(value, default=""):
    if value is None:
        return default
    text = str(value).strip()
    return text if text else default


def parse_date(value):
    if not value:
        return datetime.now(timezone.utc)

    text = str(value).strip().replace("Z", "+00:00")

    candidates = [
        text,
        text[:10],
        text[:7] + "-01" if len(text) >= 7 else text,
        text[:4] + "-01-01" if len(text) >= 4 else text,
    ]

    for candidate in candidates:
        try:
            dt = datetime.fromisoformat(candidate)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except ValueError:
            pass

    return datetime.now(timezone.utc)


def rss_date(dt):
    return format_datetime(dt.astimezone(timezone.utc), usegmt=True)


def fetch_items():
    params = {
        "literatureType": "JOURNAL",
        "relevance": ["GBIF_USED", "GBIF_CITED"],
        "limit": MAX_ITEMS,
        "offset": 0,
    }

    url = API_URL + "?" + urllib.parse.urlencode(params, doseq=True)

    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "gbif-literature-feed/1.0",
        },
    )

    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        if response.status != 200:
            raise RuntimeError(f"GBIF API error: HTTP {response.status}")

        data = json.loads(response.read().decode("utf-8"))

    results = data.get("results")
    if not isinstance(results, list):
        raise RuntimeError("GBIF API response does not contain results list")

    return results


def get_key(item):
    return get_text(item.get("key") or item.get("id") or item.get("uuid"))


def get_title(item):
    return get_text(
        item.get("title") or item.get("citation") or item.get("name"),
        "Untitled GBIF literature item",
    )


def get_doi(item):
    doi = get_text(item.get("doi") or item.get("DOI"))
    if doi:
        return doi.replace("https://doi.org/", "").replace("http://doi.org/", "")

    identifiers = item.get("identifiers")
    if isinstance(identifiers, list):
        for identifier in identifiers:
            text = get_text(identifier)
            if text.startswith("10."):
                return text
            if "doi.org/" in text:
                return text.split("doi.org/")[-1]

    return ""


def get_journal(item):
    return get_text(
        item.get("journal")
        or item.get("source")
        or item.get("containerTitle")
        or item.get("publisher")
    )


def get_link(item, key, doi):
    if key:
        return "https://www.gbif.org/literature/" + urllib.parse.quote(str(key))

    if doi:
        return "https://doi.org/" + urllib.parse.quote(doi)

    url = get_text(item.get("url") or item.get("link"))
    if url.startswith("http://") or url.startswith("https://"):
        return url

    return SITE_URL


def get_pub_date(item):
    return parse_date(item.get("modified") or item.get("publicationDate"))


def make_description(item, doi, journal, pub_dt):
    lines = []

    citation = get_text(item.get("citation"))
    if citation:
        lines.append("Citation: " + citation)

    if journal:
        lines.append("Journal / Source: " + journal)

    if doi:
        lines.append("DOI: https://doi.org/" + doi)

    lines.append("Publication date: " + pub_dt.date().isoformat())

    relevance = item.get("relevance")
    if isinstance(relevance, list):
        lines.append("GBIF relevance: " + ", ".join(str(x) for x in relevance))
    elif relevance:
        lines.append("GBIF relevance: " + str(relevance))

    return "\n".join(lines)


def remove_duplicates(items):
    seen = set()
    unique = []

    for item in items:
        key = get_key(item)
        doi = get_doi(item).lower()
        title = get_title(item).lower()

        if key:
            unique_id = "key:" + str(key)
        elif doi:
            unique_id = "doi:" + doi
        else:
            unique_id = "title:" + title

        if unique_id in seen:
            continue

        seen.add(unique_id)
        unique.append(item)

    return unique


def build_feed(items):
    rss = ET.Element("rss", {"version": "2.0"})
    channel = ET.SubElement(rss, "channel")

    ET.SubElement(channel, "title").text = "GBIF Literature Feed"
    ET.SubElement(channel, "link").text = SITE_URL
    ET.SubElement(channel, "description").text = (
        "Journal articles from GBIF Literature API where GBIF is used or cited."
    )
    ET.SubElement(channel, "language").text = "en"
    ET.SubElement(channel, "lastBuildDate").text = rss_date(datetime.now(timezone.utc))

    # 新しい順に並べる
    items = sorted(items, key=get_pub_date, reverse=True)

    for item in items[:MAX_ITEMS]:
        key = get_key(item)
        title = get_title(item)
        doi = get_doi(item)
        journal = get_journal(item)
        pub_dt = get_pub_date(item)
        link = get_link(item, key, doi)
        description = make_description(item, doi, journal, pub_dt)

        rss_item = ET.SubElement(channel, "item")

        ET.SubElement(rss_item, "title").text = title
        ET.SubElement(rss_item, "link").text = link
        ET.SubElement(rss_item, "description").text = description
        ET.SubElement(rss_item, "pubDate").text = rss_date(pub_dt)

        guid = ET.SubElement(rss_item, "guid")
        guid.set("isPermaLink", "false")

        if key:
            guid.text = "gbif-literature:" + str(key)
        elif doi:
            guid.text = "doi:" + doi
        else:
            guid.text = "title:" + title

        if journal:
            ET.SubElement(rss_item, "category").text = journal

    return ET.ElementTree(rss)


def main():
    try:
        items = fetch_items()
        items = remove_duplicates(items)

        if not items:
            raise RuntimeError("No literature items found")

        feed = build_feed(items)
        feed.write(OUTPUT_FILE, encoding="utf-8", xml_declaration=True)

        print("Generated feed.xml")
        print("Items:", len(items[:MAX_ITEMS]))

    except Exception as e:
        print("ERROR:", e, file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()

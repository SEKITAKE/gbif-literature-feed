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


def text(value, default=""):
    if value is None:
        return default
    value = str(value).strip()
    return value if value else default


def parse_date(value):
    if not value:
        return datetime.now(timezone.utc)

    value = str(value).strip().replace("Z", "+00:00")

    candidates = [
        value,
        value[:10],
        value[:7] + "-01" if len(value) >= 7 else value,
        value[:4] + "-01-01" if len(value) >= 4 else value,
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
            raise RuntimeError("GBIF API error: HTTP " + str(response.status))

        data = json.loads(response.read().decode("utf-8"))

    results = data.get("results")

    if not isinstance(results, list):
        raise RuntimeError("GBIF API response does not contain results list")

    return results


def get_title(item):
    return text(
        item.get("title") or item.get("citation") or item.get("name"),
        "Untitled GBIF literature item",
    )


def get_id(item):
    return text(item.get("id") or item.get("key") or item.get("uuid"))


def get_doi(item):
    """
    GBIF Literature API では DOI が identifiers.doi に入っている。
    例:
    "identifiers": {
      "doi": "10.1111/mam.70025"
    }
    """
    identifiers = item.get("identifiers")

    if isinstance(identifiers, dict):
        doi = text(identifiers.get("doi"))
        if doi:
            return doi.replace("https://doi.org/", "").replace("http://doi.org/", "")

    doi = text(item.get("doi") or item.get("DOI"))
    if doi:
        return doi.replace("https://doi.org/", "").replace("http://doi.org/", "")

    return ""


def get_doi_url(item):
    doi = get_doi(item)

    if doi:
        return "https://doi.org/" + doi

    return ""


def get_journal(item):
    return text(
        item.get("source")
        or item.get("journal")
        or item.get("containerTitle")
        or item.get("publisher")
    )


def get_pub_date(item):
    return parse_date(
        item.get("modified")
        or item.get("published")
        or item.get("publicationDate")
        or item.get("added")
    )


def get_authors(item):
    authors = item.get("authors")

    if not isinstance(authors, list):
        return ""

    names = []

    for author in authors:
        if isinstance(author, dict):
            first = text(author.get("firstName"))
            last = text(author.get("lastName"))
            name = (first + " " + last).strip()
            if name:
                names.append(name)

    return ", ".join(names[:5])


def make_description(item):
    doi_url = get_doi_url(item)
    journal = get_journal(item)
    authors = get_authors(item)
    published = text(item.get("published"))
    publisher = text(item.get("publisher"))
    relevance = item.get("relevance")

    lines = []

    if doi_url:
        lines.append("DOI: " + doi_url)

    if journal:
        lines.append("Journal: " + journal)

    if publisher:
        lines.append("Publisher: " + publisher)

    if published:
        lines.append("Published: " + published)

    if authors:
        lines.append("Authors: " + authors)

    if isinstance(relevance, list):
        lines.append("GBIF relevance: " + ", ".join(str(x) for x in relevance))

    abstract = text(item.get("abstract"))
    if abstract:
        lines.append("")
        lines.append(abstract[:500])

    return "\n".join(lines)


def remove_duplicates(items):
    seen = set()
    unique = []

    for item in items:
        item_id = get_id(item)
        doi = get_doi(item).lower()
        title = get_title(item).lower()

        if item_id:
            unique_id = "id:" + item_id
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

    items = sorted(items, key=get_pub_date, reverse=True)

    for item in items[:MAX_ITEMS]:
        title = get_title(item)
        item_id = get_id(item)
        doi = get_doi(item)
        doi_url = get_doi_url(item)
        pub_dt = get_pub_date(item)
        description = make_description(item)

        rss_item = ET.SubElement(channel, "item")

        ET.SubElement(rss_item, "title").text = title

        if doi_url:
            ET.SubElement(rss_item, "link").text = doi_url
        elif item_id:
            ET.SubElement(rss_item, "link").text = "https://www.gbif.org/literature/" + urllib.parse.quote(item_id)
        else:
            ET.SubElement(rss_item, "link").text = SITE_URL

        ET.SubElement(rss_item, "description").text = description
        ET.SubElement(rss_item, "pubDate").text = rss_date(pub_dt)

        guid = ET.SubElement(rss_item, "guid")
        guid.set("isPermaLink", "false")

        if item_id:
            guid.text = "gbif-literature:" + item_id
        elif doi:
            guid.text = "doi:" + doi
        else:
            guid.text = "title:" + title

        journal = get_journal(item)
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
        print("Items:", min(len(items), MAX_ITEMS))

    except Exception as e:
        print("ERROR:", e, file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()

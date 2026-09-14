"""Fetch a GitHub repo's README and parse its job table(s) into Job objects.

The popular internship/new-grad list repos (SimplifyJobs, vanshb03, etc.) all use
GitHub-Flavored-Markdown tables, but they're messy in consistent ways:

  * Links come as markdown ``[Apply](url)`` OR HTML ``<a href="url">`` OR an
    ``<img>`` "apply" button wrapped in an anchor.
  * A role under the same company as the row above uses a continuation marker in
    the company cell: ``↳``, ``"``, ``⤷``, or just blank.
  * Closed roles are flagged with 🔒 / ❌ / "Closed".
  * Column order varies, so we map columns by matching the header row.

We keep this dependency-light: just ``requests`` + regex. That's deliberate — a
table parser you can read top-to-bottom beats a heavy HTML/markdown lib you can't.
"""
from __future__ import annotations

import re
from typing import List, Optional
from urllib.parse import urlparse

import requests

from .models import Job

# --- header synonyms: map a repo's column names onto our fields ----------------
_COLUMN_ALIASES = {
    "company": {"company", "name", "employer", "organization", "org"},
    "title": {"role", "title", "position", "job title", "role title"},
    "location": {"location", "locations", "loc"},
    "url": {"application", "application/link", "apply", "link", "links", "apply/link"},
    "posted": {"date", "date posted", "posted", "age"},
}

_CONTINUATION = {"↳", "⤷", "\"", "''", "”", "same", "same as above", "^"}
_CLOSED_MARKERS = ("🔒", "❌", "🚫")

# Markdown link:  [text](url)    HTML anchor:  <a href="url">   Bare http(s) url
_MD_LINK = re.compile(r"\[[^\]]*\]\((https?://[^)\s]+)\)")
_HTML_HREF = re.compile(r'href=["\'](https?://[^"\']+)["\']', re.I)
_BARE_URL = re.compile(r"(https?://[^\s|<>\"')]+)")
_TAG = re.compile(r"<[^>]+>")
_MD_EMPHASIS = re.compile(r"[*_`]+")


def to_raw_url(url: str) -> str:
    """Accept any github.com repo/blob URL or a raw URL; return a raw README URL."""
    url = url.strip()
    if "raw.githubusercontent.com" in url:
        return url
    p = urlparse(url)
    parts = [x for x in p.path.split("/") if x]
    if p.netloc.endswith("github.com") and len(parts) >= 2:
        owner, repo = parts[0], parts[1]
        # .../blob/<branch>/<path>  -> raw of that exact file
        if len(parts) >= 4 and parts[2] == "blob":
            branch = parts[3]
            rest = "/".join(parts[4:]) or "README.md"
            return f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{rest}"
        # bare repo -> try README on the common default branches
        return f"https://raw.githubusercontent.com/{owner}/{repo}/HEAD/README.md"
    return url  # assume caller gave us something fetchable


def fetch_readme(url: str, timeout: int = 20) -> str:
    raw = to_raw_url(url)
    r = requests.get(raw, timeout=timeout, headers={"User-Agent": "job-apply-assistant"})
    # HEAD ref sometimes 404s on older repos; fall back to main then master.
    if r.status_code == 404 and "/HEAD/" in raw:
        for branch in ("main", "master"):
            alt = raw.replace("/HEAD/", f"/{branch}/")
            r = requests.get(alt, timeout=timeout, headers={"User-Agent": "job-apply-assistant"})
            if r.ok:
                break
    r.raise_for_status()
    return r.text


def _split_row(line: str) -> List[str]:
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|"):
        line = line[:-1]
    # split on unescaped pipes
    return [c.strip() for c in re.split(r"(?<!\\)\|", line)]


# Leading "trending"/decorative emoji and symbols repos prepend to a company.
# Strip a leading run of non-alphanumerics, so "🔥 Notion" -> "Notion" while
# "1Password" (starts with a digit) is preserved.
_LEAD_DECOR = re.compile(r"^[^0-9A-Za-z]+")


def _clean_company(cell: str) -> str:
    """Strip a leading emoji/symbol run (🔥, ⭐, ✨) but keep names like '1Password'."""
    txt = _clean_text(cell)
    stripped = _LEAD_DECOR.sub("", txt)
    return stripped if stripped else txt   # don't blank out a name that's all symbols


def _clean_text(cell: str) -> str:
    # markdown link -> its label; strip tags/images/emphasis
    cell = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", cell)
    cell = _TAG.sub("", cell)
    cell = _MD_EMPHASIS.sub("", cell)
    cell = cell.replace("\\|", "|")
    return cell.strip()


def _first_url(cell: str) -> Optional[str]:
    for pat in (_MD_LINK, _HTML_HREF, _BARE_URL):
        m = pat.search(cell)
        if m:
            return m.group(1)
    return None


def _is_separator(cells: List[str]) -> bool:
    return all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c)


def _map_columns(header: List[str]) -> dict:
    mapping = {}
    for idx, name in enumerate(header):
        key = _clean_text(name).lower().strip()
        for field, aliases in _COLUMN_ALIASES.items():
            if key in aliases and field not in mapping:
                mapping[field] = idx
    return mapping


# --- HTML tables --------------------------------------------------------------
# Modern SimplifyJobs / Pitt-CSC repos render their listings as real <table>
# HTML rather than markdown pipes, so we parse those too. Cells here never nest
# another table, so tag-based splitting with regex is safe and readable.
_TABLE = re.compile(r"<table\b[^>]*>(.*?)</table>", re.I | re.S)
_ROW = re.compile(r"<tr\b[^>]*>(.*?)</tr>", re.I | re.S)
_CELL = re.compile(r"<t[dh]\b[^>]*>(.*?)</t[dh]>", re.I | re.S)


def _cell_text(html: str) -> str:
    # <br> -> " / " so "SF<br>NYC" reads as "SF / NYC", then strip remaining tags
    html = re.sub(r"<br\s*/?>", " / ", html, flags=re.I)
    return _clean_text(html).strip(" /")


def _best_apply_url(html: str) -> Optional[str]:
    """Prefer the real employer link over the list's own tracking/autofill link."""
    hrefs = _HTML_HREF.findall(html) or ([m] if (m := _first_url(html)) else [])
    for h in hrefs:
        if "simplify.jobs" not in h.lower():
            return h
    return hrefs[0] if hrefs else None


def _parse_html_tables(text: str, source: str, include_closed: bool) -> List[Job]:
    jobs: List[Job] = []
    for tbl in _TABLE.findall(text):
        rows = _ROW.findall(tbl)
        if not rows:
            continue
        header = [_clean_text(c).lower().strip() for c in _CELL.findall(rows[0])]
        mapping = _map_columns(header)
        if "company" not in mapping or "title" not in mapping:
            continue
        last_company = ""
        for row in rows[1:]:
            cells = _CELL.findall(row)
            if not cells:
                continue

            def col(field: str) -> str:
                idx = mapping.get(field)
                return cells[idx] if idx is not None and idx < len(cells) else ""

            company_raw = _clean_company(col("company"))
            company = last_company if company_raw in _CONTINUATION or not company_raw else company_raw
            if company_raw and company_raw not in _CONTINUATION:
                last_company = company

            title = _cell_text(col("title"))
            url = _best_apply_url(col("url"))
            if any(m in row for m in _CLOSED_MARKERS) and not include_closed:
                continue
            if not (url and company and title):
                continue
            jobs.append(Job(company=company, title=title, url=url,
                            location=_cell_text(col("location")),
                            posted=_cell_text(col("posted")), source=source))
    return jobs


def parse_readme(text: str, source: str = "", include_closed: bool = False) -> List[Job]:
    """Walk the document, find every table (markdown OR HTML), and emit Jobs.

    Multiple tables (a repo split into "Software", "Quant", "Hardware") are all
    parsed; we re-detect the header each time the column layout resets.
    """
    html_jobs = _parse_html_tables(text, source, include_closed)

    lines = text.splitlines()
    jobs: List[Job] = []
    mapping: dict = {}
    header_len = 0
    last_company = ""

    i = 0
    while i < len(lines):
        line = lines[i]
        if "|" not in line:
            mapping = {}          # a gap ends the current table
            i += 1
            continue

        cells = _split_row(line)
        # A header row is one whose next line is a |---|---| separator.
        if i + 1 < len(lines) and "|" in lines[i + 1]:
            nxt = _split_row(lines[i + 1])
            if _is_separator(nxt):
                mapping = _map_columns(cells)
                header_len = len(cells)
                last_company = ""
                i += 2
                continue

        if not mapping:           # a | line that isn't part of a table we understand
            i += 1
            continue
        if _is_separator(cells):
            i += 1
            continue

        # pad short rows so index lookups are safe
        if len(cells) < header_len:
            cells += [""] * (header_len - len(cells))

        def col(field: str) -> str:
            idx = mapping.get(field)
            return cells[idx] if idx is not None and idx < len(cells) else ""

        company_raw = _clean_company(col("company"))
        if company_raw in _CONTINUATION or company_raw == "":
            company = last_company
        else:
            company = company_raw
            last_company = company

        title = _clean_text(col("title"))
        url = _first_url(col("url")) or _first_url(line) or ""
        row_text = line

        # skip closed / no-link rows
        is_closed = any(m in row_text for m in _CLOSED_MARKERS)
        if is_closed and not include_closed:
            i += 1
            continue
        if not url or not company or not title:
            i += 1
            continue

        jobs.append(Job(
            company=company,
            title=title,
            url=url,
            location=_clean_text(col("location")),
            posted=_clean_text(col("posted")),
            source=source,
        ))
        i += 1

    # Merge markdown + HTML results, de-duplicating on the job's stable key.
    seen, merged = set(), []
    for j in html_jobs + jobs:
        if j.key in seen:
            continue
        seen.add(j.key)
        merged.append(j)
    return merged

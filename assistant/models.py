"""Shared data structures.

Mirrors the shape of ~/jobscraper's Job so the two tools stay familiar, but adds
the fields an *apply funnel* needs: a stable key for dedupe, a status, and notes.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Optional

# The funnel states a job moves through. Keep this small and linear-ish so the
# CLI stays legible. "new" -> you haven't looked; the rest are your decisions.
STATUSES = ("new", "drafted", "applied", "interview", "offer", "rejected", "skipped")

# Role categories, in the priority order you want to work them: SWE first, then
# data + ML (one combined bucket), then PM, then everything else. `next` and
# `list` order by this.
CATEGORY_ORDER = ("swe", "data-ml", "pm", "other")

# Title-keyword rules, checked top to bottom; first match wins. PM and data-ml are
# checked before SWE because their titles are more specific — a "Machine Learning
# Software Engineer" should land in data-ml, not get pulled into swe.
_CATEGORY_RULES = (
    ("pm", ("product manager", "product management", "associate product",
            "product intern", "apm", "product owner")),
    ("data-ml", ("data scien", "data analyst", "data engineer", "data intern",
                 "machine learning", "deep learning", "analytics",
                 "ml engineer", "ml intern", "ml/ai", "ai/ml", "ai engineer",
                 "ai research", "applied ai", "applied scien", "artificial intelligence",
                 "genai", "generative ai", "llm", "nlp", "computer vision",
                 " ai ", " ml ", "research scien", "quantitative", "quant ")),
    ("swe", ("software eng", "software dev", "swe", "developer", "back end",
             "backend", "front end", "frontend", "full stack", "full-stack",
             "web dev", "mobile", "ios", "android", "platform eng",
             "systems eng", "infrastructure", "devops", "site reliability",
             "sre", "security eng", "embedded", "firmware")),
)


def categorize(title: str) -> str:
    """Map a job title to one of CATEGORY_ORDER. Unknown titles -> 'other'."""
    t = f" {title.lower()} "
    for cat, keywords in _CATEGORY_RULES:
        if any(k in t for k in keywords):
            return cat
    return "other"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Job:
    company: str
    title: str
    url: str
    location: str = ""
    posted: str = ""              # human string as printed in the repo table
    source: str = ""              # which repo/list this came from
    status: str = "new"
    applied_at: str = ""          # ISO timestamp, set when you mark it applied
    notes: str = ""
    draft_path: str = ""          # path to the generated cover-letter/intro draft
    category: str = ""            # swe | data | pm | other, derived from title
    first_seen: str = field(default_factory=_now)

    def __post_init__(self) -> None:
        # Always derive category from the title so it stays correct whether the
        # Job comes from the parser or is rebuilt from the database.
        self.category = categorize(self.title)

    @property
    def is_grad(self) -> bool:
        """True for advanced-degree roles (PhD/Masters/MBA), which don't fit an
        undergrad. The repo flags these with 🎓; we also catch explicit mentions.
        "graduate" alone is deliberately excluded — "New Graduate" means bachelor's.
        """
        t = f" {self.title.lower()} "
        return "🎓" in t or any(k in t for k in (
            "phd", "ph.d", "doctoral", "advanced degree", " mba ", "masters "))

    @property
    def key(self) -> str:
        """Stable identity for dedupe across re-syncs.

        We can't trust the URL alone (repos rotate tracking links) or the company
        alone (many roles per company), so we hash company+title+host. If a repo
        genuinely re-lists the same role under a new link, this still collapses it.
        """
        host = self.url.split("//")[-1].split("/")[0].lower()
        raw = f"{self.company.strip().lower()}|{self.title.strip().lower()}|{host}"
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]

    def to_row(self) -> dict:
        return asdict(self)

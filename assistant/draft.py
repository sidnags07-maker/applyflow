"""Generate a tailored intro / cover-letter draft for one job.

Three modes; ``make_draft`` picks the first that's available:

  * **Gemini mode** (free): if ``GEMINI_API_KEY`` / ``GOOGLE_API_KEY`` is set and
    a Google GenAI SDK is installed. Free tier, so it's the default when present.
  * **Claude mode** (paid): if ``ANTHROPIC_API_KEY`` is set and ``anthropic`` is
    installed. Highest quality; costs a fraction of a cent per letter.
  * **Template mode** (always): fills a clean skeleton from ``profile.yaml``. Zero
    cost, no network, never fails.

``draft_provider`` in config.yaml controls the order: "auto" (default,
gemini -> claude -> template), or force "gemini" / "claude" / "template".

Either way the output is a *draft you review*, never something auto-sent. The
draft is written to ``drafts/<company>-<title>.md`` and its path saved on the job.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Optional

from .models import Job

CLAUDE_MODEL = "claude-sonnet-5"     # current, capable, cheap-per-letter
GEMINI_MODEL = "gemini-2.5-flash"    # free-tier eligible; change if your key lacks it


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:60] or "job"


def _header(job: Job) -> str:
    return (f"# {job.company}: {job.title}\n\n**Link:** {job.url}\n"
            f"**Location:** {job.location or '-'}\n\n---\n\n")


def _prompt(job: Job, profile: dict) -> str:
    return f"""Write a short, specific, non-generic cover letter (about 150 words)
for this job application. Sound like a real early-career candidate, not a template.
No em dashes.

If web search is available to you, first search for what {job.company} actually
builds or is known for, then reference one real, specific detail about them. Do
not invent facts about the company. If you cannot find something specific, keep
the company mention modest rather than making something up.

CRUCIAL — build the "why this company" angle as Spark -> Bridge -> Value:
  1. SPARK: one real, researched thing about {job.company} (a product, a mission,
     a technical detail). Not generic flattery.
  2. BRIDGE: tie that directly to something the candidate has actually built or
     done (a concrete deployed project of theirs). This is the connect-back.
  3. VALUE: end on what the candidate would contribute — how they'd use those same
     skills to help {job.company} succeed, not just what they'd get out of it.
The company detail is only the setup; the payoff is the candidate's real work and
what they'd bring. Never leave a "why us" that only praises the company.

Open by leading with the candidate's single strongest shipped project (a concrete,
deployed thing), not a generic "I am a passionate student" line. You may mention
the candidate's GitHub or LinkedIn if it reads naturally.

CANDIDATE PROFILE:
{profile}

JOB:
Company: {job.company}
Title: {job.title}
Location: {job.location}
Link: {job.url}

Return only the letter body, no preamble."""


# --- provider backends: each returns a finished draft string, or None ----------
def _template_letter(job: Job, profile: dict) -> str:
    name = profile.get("name", "")
    tagline = profile.get("tagline", "")
    bullets = "\n".join(f"- {h}" for h in (profile.get("highlights", []) or []))
    skills = ", ".join(profile.get("skills", []) or [])
    return _header(job) + f"""Dear {job.company} Hiring Team,

I'm {name}, {tagline}. I'm excited to apply for the {job.title} role.

{bullets}

I work with {skills}, and I'm drawn to {job.company} because [ONE SPECIFIC
REASON: what they build, a product you use, their mission]. I'd love to bring
this energy to your team.

Thank you for your time and consideration.

Best,
{name}
{profile.get("email", "")}
{_links(profile)}

---
_Draft: review the [BRACKETED] bits before you send. Add one concrete, specific
sentence about this company; that single line beats a whole generic paragraph._
"""


def _links(profile: dict) -> str:
    """Render 'GitHub: ... · LinkedIn: ...', skipping any that's blank/placeholder."""
    def real(v: str) -> bool:
        return bool(v) and "<add" not in v
    parts = []
    if real(profile.get("github", "")):
        parts.append(f"GitHub: {profile['github']}")
    if real(profile.get("linkedin", "")):
        parts.append(f"LinkedIn: {profile['linkedin']}")
    return "  ·  ".join(parts)


def _gemini_letter(job: Job, profile: dict) -> Optional[str]:
    key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not key:
        return None
    prompt = _prompt(job, profile)
    body = None
    try:
        # Preferred: the new unified SDK ("pip install google-genai")
        from google import genai
        client = genai.Client(api_key=key)
        resp = client.models.generate_content(model=GEMINI_MODEL, contents=prompt)
        body = (resp.text or "").strip()
    except ImportError:
        try:
            # Fallback: the older SDK ("pip install google-generativeai")
            import google.generativeai as genai
            genai.configure(api_key=key)
            resp = genai.GenerativeModel(GEMINI_MODEL).generate_content(prompt)
            body = (resp.text or "").strip()
        except ImportError:
            print("  (no Google GenAI SDK; run: pip install google-genai)")
            return None
        except Exception as e:
            print(f"  (Gemini drafting failed: {e}; falling back)")
            return None
    except Exception as e:
        print(f"  (Gemini drafting failed: {e}; falling back)")
        return None
    return _header(job) + body + "\n" if body else None


def _claude_letter(job: Job, profile: dict) -> Optional[str]:
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        return None
    try:
        import anthropic
    except ImportError:
        print("  (no anthropic SDK; run: pip install anthropic)")
        return None

    client = anthropic.Anthropic(api_key=key)

    def generate(with_search: bool) -> str:
        """One drafting run. Web search is a server-side tool: Claude issues the
        searches on Anthropic's side and the results return as content blocks in
        the same response. A long search loop can end a turn with
        stop_reason 'pause_turn'; we resend to resume, capped so a stuck loop
        can't run forever."""
        tool_kw = {"tools": [{"type": "web_search_20260209",
                              "name": "web_search", "max_uses": 3}]} if with_search else {}
        msgs = [{"role": "user", "content": _prompt(job, profile)}]
        for _ in range(6):
            resp = client.messages.create(
                model=CLAUDE_MODEL, max_tokens=1024, messages=msgs, **tool_kw)
            if resp.stop_reason == "pause_turn":
                msgs = msgs + [{"role": "assistant", "content": resp.content}]
                continue
            return "".join(b.text for b in resp.content
                           if getattr(b, "type", "") == "text").strip()
        return ""

    try:
        body = generate(with_search=True)
    except Exception as e:
        # Search may be unavailable (older SDK, plan limits); draft without it
        # rather than dropping all the way to the offline template.
        print(f"  (web search unavailable: {e}; drafting without it)")
        try:
            body = generate(with_search=False)
        except Exception as e2:
            print(f"  (Claude drafting failed: {e2}; falling back)")
            return None
    return _header(job) + body + "\n" if body else None


_PROVIDERS = {"gemini": _gemini_letter, "claude": _claude_letter}
# Order tried when provider == "auto": free first, then paid, then template.
_AUTO_ORDER = ("gemini", "claude")


def make_draft(job: Job, profile: dict, out_dir: str, provider: str = "auto") -> str:
    Path(out_dir).mkdir(parents=True, exist_ok=True)

    text: Optional[str] = None
    if provider == "template":
        text = None
    elif provider in _PROVIDERS:
        text = _PROVIDERS[provider](job, profile)
    else:  # auto
        for name in _AUTO_ORDER:
            text = _PROVIDERS[name](job, profile)
            if text:
                break
    if not text:
        text = _template_letter(job, profile)

    path = str(Path(out_dir) / f"{_slug(job.company)}-{_slug(job.title)}.md")
    Path(path).write_text(text, encoding="utf-8")
    return path

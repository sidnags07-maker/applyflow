#!/usr/bin/env python3
"""Phase 1 autofiller (Ashby).

Fills an Ashby application's STANDARD fields from application_profile.yaml and
uploads your resume, then STOPS. It never clicks Submit — it leaves the browser
open so you review every field and submit yourself.

    python autofill.py <job-key | application-url>

What it fills (the reliable, consistently-labeled fields):
  full name, email, phone, location (autocomplete), LinkedIn, GitHub, resume.
Custom / Yes-No / essay questions are left blank on purpose — those are Phase 2
(the API step) and the highest-risk to get wrong, so they stay for you.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import yaml
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

from assistant.store import Store

HERE = Path(__file__).resolve().parent


def load_env() -> None:
    """Load KEY=value lines from .env into the environment (real env wins)."""
    p = HERE / ".env"
    if not p.exists():
        return
    for line in p.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def load_profile() -> dict:
    return yaml.safe_load((HERE / "application_profile.yaml").read_text())


def resolve_url(arg: str) -> str:
    if arg.startswith("http"):
        return arg.split("?")[0]
    job = Store(str(HERE / "funnel.db")).get(arg)
    if not job:
        sys.exit(f"No job matches key {arg!r}")
    print(f"==> {job.company} — {job.title}")
    return job.url.split("?")[0]


def _by_label(page, label: str):
    """Find the input/textarea that belongs to a visible field label.

    Ashby labels the input by its placeholder, not the field heading, so
    get_by_label often misses. We locate the heading text (tolerating a trailing
    "*" required marker), then take the first input/textarea/combobox that follows
    it in the DOM.
    """
    # normalize-space(text()) trims whitespace; the "*" lives in a sibling span so
    # the label's own text node is just the field name.
    xpath = (f'xpath=//*[normalize-space(text())="{label}"]/'
             f'following::*[self::input or self::textarea or @role="combobox"][1]')
    return page.locator(xpath).first


def _fill_first(page, labels, value, name, log) -> bool:
    """Try a list of candidate labels; fill the first one that exists."""
    if not value:
        return False
    for label in labels:
        loc = _by_label(page, label)
        try:
            if loc.count():
                loc.scroll_into_view_if_needed()
                loc.fill(str(value))
                log(f"  ✓ {name} (via '{label}'): {value}")
                return True
        except Exception as e:
            log(f"  ! {name} (via '{label}'): {e}")
    log(f"  – {name}: no matching label {labels}")
    return False


def _fill(page, label: str, value, log) -> bool:
    if not value:
        return False
    loc = _by_label(page, label)
    try:
        if loc.count():
            loc.scroll_into_view_if_needed()
            loc.fill(str(value))
            log(f"  ✓ {label}: {value}")
            return True
    except Exception as e:
        log(f"  ! {label}: {e}")
    log(f"  – {label}: not found")
    return False


def _fill_by_type(page, css: str, value, name, log) -> bool:
    if not value:
        return False
    loc = page.locator(css).first
    try:
        if loc.count():
            loc.fill(str(value))
            log(f"  ✓ {name}: {value}")
            return True
    except Exception as e:
        log(f"  ! {name}: {e}")
    return False


def _fill_location(page, value, log):
    """Location is an autocomplete: type, wait for the dropdown, click option 1."""
    if not value:
        return
    combo = page.locator('[role="combobox"], input[placeholder*="Start typing"]').first
    try:
        if not combo.count():
            log("  – location: combobox not found")
            return
        combo.scroll_into_view_if_needed()
        combo.click()
        combo.fill(value.split(",")[0])          # "Bloomington"
        page.wait_for_timeout(1200)              # let suggestions load
        option = page.locator('[role="option"]').first
        if option.count():
            txt = option.inner_text().strip()    # read BEFORE click (option vanishes after)
            option.click()
            log(f"  ✓ location: {txt}")
        else:
            log("  – location: no suggestions appeared (fill left as typed)")
    except Exception as e:
        log(f"  ! location: {e}")


def _upload_resume(page, path: str, log):
    p = Path(path)
    if not p.exists():
        log(f"  ! resume file not found: {path}")
        return
    inputs = page.locator('input[type="file"]')
    n = inputs.count()
    if n == 0:
        log("  – resume: no file input found")
        return
    # The "Autofill from resume" input is usually first; the real Resume field is
    # last. Prefer the last file input.
    try:
        inputs.nth(n - 1).set_input_files(str(p))
        log(f"  ✓ resume uploaded: {p.name}")
    except Exception as e:
        log(f"  ! resume: {e}")


def fill_form(page, profile: dict, log) -> None:
    """Dispatch to the right ATS filler based on the page URL. Never submits."""
    if "greenhouse.io" in (page.url or ""):
        _fill_greenhouse(page, profile, log)
    else:
        _fill_ashby(page, profile, log)


def _fill_id(page, css_id: str, value, name, log) -> bool:
    if not value:
        return False
    loc = page.locator(css_id).first
    try:
        if loc.count():
            loc.fill(str(value))
            log(f"  ✓ {name}: {value}")
            return True
    except Exception as e:
        log(f"  ! {name}: {e}")
    log(f"  – {name}: not found ({css_id})")
    return False


def _fill_greenhouse(page, profile: dict, log) -> None:
    page.wait_for_timeout(2500)
    log("==> filling standard fields (Greenhouse):")
    full = (profile.get("full_name") or "").strip()
    first, _, last = full.rpartition(" ")
    _fill_id(page, "#first_name", first or full, "first name", log)
    _fill_id(page, "#last_name", last, "last name", log)
    _fill_id(page, "#email", profile.get("email"), "email", log)
    _fill_id(page, "#phone", profile.get("phone"), "phone", log)
    _fill_id(page, "#school--0", profile.get("school"), "school", log)
    resume = Path(profile.get("resume_path", ""))
    if resume.exists():
        try:
            page.locator("#resume").first.set_input_files(str(resume))
            log(f"  ✓ resume uploaded: {resume.name}")
        except Exception as e:
            log(f"  ! resume: {e}")
    else:
        log(f"  ! resume file not found: {resume}")


def _fill_ashby(page, profile: dict, log) -> None:
    """Fill one already-loaded Ashby application's standard fields."""
    page.wait_for_timeout(2500)  # let the React form render
    log("==> filling standard fields (Ashby):")
    _fill_first(page, ["Name", "First and Last Name", "Full Name", "Legal Name",
                       "Full legal name"], profile.get("full_name"), "name", log)
    _fill_by_type(page, 'input[type="email"]', profile.get("email"), "email", log)
    _fill_by_type(page, 'input[type="tel"]', profile.get("phone"), "phone", log)
    _fill_location(page, profile.get("current_location"), log)
    _fill_first(page, ["LinkedIn Profile", "LinkedIn", "LinkedIn URL",
                       "LinkedIn Profile URL"], profile.get("linkedin"), "linkedin", log)
    _fill_first(page, ["GitHub Profile URL", "Github Link", "GitHub", "Github",
                       "GitHub URL", "GitHub Profile"], profile.get("github"), "github", log)
    _upload_resume(page, profile.get("resume_path", ""), log)


# ---------------------------------------------------------------------------
# Phase 2: answer the form's custom text/essay questions via the Claude API.
# Auto-answers empty text/textarea questions; flags anything it can't answer
# confidently (unknown facts, non-text widgets) for you to handle.
# ---------------------------------------------------------------------------

CLAUDE_MODEL = "claude-sonnet-5"

_EXTRACT_JS = r"""
() => {
  const out = [];
  let i = 0;
  const seen = new Set();
  document.querySelectorAll('textarea, input[type="text"], input:not([type])').forEach(el => {
    if (el.value && el.value.trim()) return;      // already filled by Phase 1
    if (el.offsetParent === null) return;          // hidden
    // Skip react-select / combobox search inputs (Greenhouse dropdowns) and
    // autocompletes — typing free text into those corrupts the widget.
    if (el.readOnly) return;
    if (el.getAttribute('role') === 'combobox') return;
    if (el.getAttribute('aria-autocomplete')) return;
    if (el.closest('[class*="select__"]')) return;
    let label = '';
    if (el.labels && el.labels.length) label = el.labels[0].innerText;
    if (!label && el.getAttribute('aria-label')) label = el.getAttribute('aria-label');
    if (!label) {                                  // fallback: nearest preceding text
      let node = el, hops = 0;
      while (node && hops < 6 && !label) {
        let sib = node.previousElementSibling;
        while (sib) {
          const t = (sib.innerText || '').trim();
          if (t && t.length < 160) { label = t; break; }
          sib = sib.previousElementSibling;
        }
        node = node.parentElement; hops++;
      }
    }
    label = (label || '').replace(/\*/g, '').trim();
    if (!label || seen.has(label)) return;
    // Skip conditional follow-ups ("If yes...", "If other, please specify") -
    // they must stay blank unless the triggering answer was chosen.
    if (/^if\b/i.test(label)) return;
    seen.add(label);
    const id = 'afq' + (i++);
    el.setAttribute('data-afq', id);
    out.push({id, label, tag: el.tagName.toLowerCase()});
  });
  return out;
}
"""


def _extract_questions(page):
    try:
        return page.evaluate(_EXTRACT_JS)
    except Exception as e:
        print(f"  ! question extraction failed: {e}")
        return []


def _answer_questions(profile, answer_bank, company, title, questions):
    """One Claude call (with web search) to answer all custom questions. Returns
    {id: {"answer": str} | {"flag": reason}}."""
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        print("  ! no ANTHROPIC_API_KEY; skipping Phase 2 (custom questions left blank)")
        return {}
    try:
        import anthropic
    except ImportError:
        print("  ! anthropic SDK missing; skipping Phase 2")
        return {}

    qlist = "\n".join(f'  - id={q["id"]}: "{q["label"]}"' for q in questions)
    prompt = f"""You are filling a job application for this candidate. Answer each
form question below from the candidate's profile and answer bank. Company: {company}.
Role: {title}.

Rules:
- If a question is a fact you don't have (e.g. "how many prior internships", exact
  graduation month, "have you used our product"), do NOT guess. Flag it.
- For a "why do you want to work at {company}" question: use web search to find one
  real specific thing about {company}, then Spark -> Bridge -> Value (real company
  detail -> tie to something the candidate actually built -> what they'd contribute).
  No em dashes. No invented company facts.
- Keep answers concise and true to the candidate.

CANDIDATE PROFILE:
{profile}

ANSWER BANK:
{answer_bank}

QUESTIONS:
{qlist}

Return ONLY a JSON object mapping each id to either {{"answer": "..."}} or
{{"flag": "short reason you can't answer"}}. No prose outside the JSON."""

    try:
        client = anthropic.Anthropic(api_key=key)
        msg = client.messages.create(
            model=CLAUDE_MODEL, max_tokens=2000,
            tools=[{"type": "web_search_20260209", "name": "web_search", "max_uses": 3}],
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
        start, end = text.find("{"), text.rfind("}")
        return json.loads(text[start:end + 1]) if start >= 0 else {}
    except Exception as e:
        print(f"  ! Phase 2 API call failed: {e}")
        return {}


def fill_custom(page, profile, company, title, log):
    questions = _extract_questions(page)
    if not questions:
        log("  (no custom text questions detected)")
        return
    log(f"==> Phase 2: {len(questions)} custom question(s); asking Claude...")
    bank_path = HERE / "application_answer_bank.md"
    answer_bank = bank_path.read_text() if bank_path.exists() else ""
    answers = _answer_questions(profile, answer_bank, company, title, questions)
    for q in questions:
        a = answers.get(q["id"]) or {}
        loc = page.locator(f'[data-afq="{q["id"]}"]').first
        if a.get("answer"):
            try:
                loc.fill(a["answer"])
                log(f"  ✓ answered: {q['label'][:55]}")
            except Exception as e:
                log(f"  ! could not fill '{q['label'][:40]}': {e}")
        else:
            reason = a.get("flag", "left for you")
            log(f"  ⚑ FLAG ({reason}): {q['label'][:55]}")


# ---------------------------------------------------------------------------
# Phase 2.5: answer the click-based questions (Yes/No buttons, radios, checkboxes)
# ---------------------------------------------------------------------------

_EXTRACT_CLICKS_JS = r"""
() => {
  let gi = 0, oi = 0;
  const groups = {};
  const OPT_SEL = 'input[type=radio],input[type=checkbox],[role=radio],[role=checkbox]';
  const isOpt = (n) => n && (
      (n.matches && n.matches(OPT_SEL)) ||
      (n.querySelector && n.querySelector(OPT_SEL)) ||
      (n.tagName === 'BUTTON' && /^(yes|no)$/i.test((n.innerText||'').trim())));
  const questionFor = (el) => {
    let node = el;
    for (let up = 0; up < 10 && node; up++) {
      let sib = node.previousElementSibling;
      while (sib) {
        if (!isOpt(sib)) {
          const t = (sib.innerText || '').trim();
          if (t && t.length > 3 && t.length < 300) return t.replace(/\*+$/, '').trim();
        }
        sib = sib.previousElementSibling;
      }
      node = node.parentElement;
    }
    return '';
  };
  const add = (el, kind, text) => {
    if (!el || !text) return;
    const q = questionFor(el);
    if (!q) return;
    if (!groups[q]) groups[q] = {qid: 'g' + (gi++), label: q, multi: kind === 'checkbox', options: []};
    let oid = el.getAttribute('data-afo');
    if (!oid) { oid = 'o' + (oi++); el.setAttribute('data-afo', oid); }
    if (!groups[q].options.some(o => o.text.toLowerCase() === text.toLowerCase()))
      groups[q].options.push({oid, text});
  };
  // Real radio/checkbox inputs are often visually hidden; click their <label>.
  document.querySelectorAll('input[type=radio],input[type=checkbox]').forEach(inp => {
    const lab = (inp.labels && inp.labels[0]) || inp.closest('label') || inp.parentElement;
    if (lab) add(lab, inp.type, (lab.innerText || '').trim());
  });
  document.querySelectorAll('[role=radio],[role=checkbox]').forEach(el => {
    if (el.offsetParent === null) return;
    add(el, el.getAttribute('role'), (el.innerText || el.getAttribute('aria-label') || '').trim());
  });
  document.querySelectorAll('button').forEach(b => {
    const t = (b.innerText || '').trim();
    if (/^(yes|no)$/i.test(t)) add(b, 'yesno', t);
  });
  return Object.values(groups).filter(g => g.options.length > 0);
}
"""


def _answer_clicks(profile, answer_bank, company, groups):
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        return {}
    try:
        import anthropic
    except ImportError:
        return {}
    slim = [{"qid": g["qid"], "label": g["label"], "multi": g["multi"],
             "options": [o["text"] for o in g["options"]]} for g in groups]
    prompt = f"""Decide how to answer each multiple-choice / dropdown question on
this job application from the candidate's profile. Company: {company}.

For each question pick the option text(s) that fit the candidate:
- Yes/No -> "Yes" or "No".
- single-select -> exactly one option text.
- multi (multi=true) -> all applicable, separated by " ;; ".
- If it is a fact you don't have (prior-internship count, "have you used our
  product", exact graduation month, anything not in the profile), FLAG it.
- Consent / agreement / "I certify" fields -> FLAG (the candidate must do those).
Do not guess.

Key facts: US citizen (authorized to work: Yes; needs sponsorship: No / None),
willing to relocate including SF and NYC, pronouns He/Him, undergraduate/Bachelor's
in Computer Science, currently resides in the United States (Indiana), found this
via a GitHub job list (so "how did you hear" -> Other if that option exists).

PROFILE:
{profile}

ANSWER BANK:
{answer_bank}

QUESTIONS:
{json.dumps(slim)}

Return ONE line per question, no other text, formatted exactly:
  <qid>||<answer>
where <answer> is the exact option text to select (multi separated by " ;; "),
or the word FLAG followed by a short reason. Examples:
  g0||Yes
  g3||New York, NY ;; San Francisco, CA
  g7||FLAG number of prior internships not in profile"""
    try:
        client = anthropic.Anthropic(api_key=key)
        msg = client.messages.create(
            model=CLAUDE_MODEL, max_tokens=1500,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
        out = {}
        for line in text.splitlines():
            if "||" not in line:
                continue
            qid, _, rhs = line.partition("||")
            qid, rhs = qid.strip(), rhs.strip()
            if rhs.upper().startswith("FLAG"):
                out[qid] = {"flag": rhs[4:].strip(" :-") or "left for you"}
            else:
                out[qid] = {"select": [v.strip() for v in rhs.split(";;") if v.strip()]}
        return out
    except Exception as e:
        print(f"  ! choice-question API call failed: {e}")
        return {}


def fill_clicks(page, profile, company, log):
    try:
        groups = page.evaluate(_EXTRACT_CLICKS_JS)
    except Exception as e:
        log(f"  ! click-question extraction failed: {e}")
        return
    if not groups:
        return
    log(f"==> Phase 2.5: {len(groups)} choice question(s); asking Claude...")
    bank_path = HERE / "application_answer_bank.md"
    bank = bank_path.read_text() if bank_path.exists() else ""
    decisions = _answer_clicks(profile, bank, company, groups)
    for g in groups:
        d = decisions.get(g["qid"]) or {}
        if not d.get("select"):
            log(f"  ⚑ FLAG ({d.get('flag', 'left for you')}): {g['label'][:55]}")
            continue
        by_text = {o["text"].strip().lower(): o["oid"] for o in g["options"]}
        for want in d["select"]:
            w = want.strip().lower()
            oid = by_text.get(w) or next((v for k, v in by_text.items() if w in k or k in w), None)
            if not oid:
                log(f"  ⚑ '{g['label'][:35]}': no option matched {want!r}")
                continue
            try:
                page.locator(f'[data-afo="{oid}"]').first.click()
                log(f"  ✓ {g['label'][:40]} -> {want}")
            except Exception as e:
                log(f"  ! click '{want}' on '{g['label'][:30]}': {e}")


# --- Greenhouse react-select dropdowns (work auth, sponsorship, pronouns, ...) --
_GH_SELECT_JS = r"""
() => {
  const out = []; let i = 0;
  document.querySelectorAll('.select__control').forEach(ctrl => {
    if (ctrl.offsetParent === null) return;
    let label = '', node = ctrl.parentElement;
    for (let up = 0; up < 5 && node && !label; up++) {
      const lab = node.querySelector('label');
      if (lab) label = (lab.innerText || '').replace(/\*/g, '').trim();
      node = node.parentElement;
    }
    if (!label || /^if\b/i.test(label)) return;
    if (/terms|conditions|certif|acknowledg|consent|i agree/i.test(label)) return;
    const id = 'afs' + (i++);
    ctrl.setAttribute('data-afs', id);
    out.push({id, label});
  });
  return out;
}
"""


def fill_gh_selects(page, profile, company, log):
    try:
        selects = page.evaluate(_GH_SELECT_JS)
    except Exception as e:
        log(f"  ! dropdown extraction failed: {e}")
        return
    if not selects:
        return
    log(f"==> Greenhouse dropdowns: {len(selects)}; asking Claude...")
    bank_path = HERE / "application_answer_bank.md"
    bank = bank_path.read_text() if bank_path.exists() else ""
    # Reuse the click-decider: it returns {qid: {"select": [text]} | {"flag": ...}}.
    groups = [{"qid": s["id"], "label": s["label"], "multi": False, "options": []}
              for s in selects]
    decisions = _answer_clicks(profile, bank, company, groups)
    for s in selects:
        d = decisions.get(s["id"]) or {}
        want = (d.get("select") or [None])[0]
        if not want:
            log(f"  ⚑ FLAG ({d.get('flag', 'left for you')}): {s['label'][:55]}")
            continue
        try:
            ctrl = page.locator(f'[data-afs="{s["id"]}"]').first
            ctrl.scroll_into_view_if_needed()
            ctrl.click()
            page.wait_for_timeout(300)
            page.keyboard.type(want)          # filter the option list
            page.wait_for_timeout(500)
            opt = page.locator(".select__option").first
            if opt.count():
                picked = opt.inner_text().strip()
                opt.click()
                log(f"  ✓ {s['label'][:40]} -> {picked}")
            else:
                page.keyboard.press("Escape")
                log(f"  ⚑ '{s['label'][:35]}': no option matched {want!r}")
        except Exception as e:
            log(f"  ! dropdown '{s['label'][:30]}': {e}")


def autofill(url: str, profile: dict, test: bool = False, smart: bool = False):
    log = print
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=test)
        page = browser.new_page()
        log(f"==> opening {url}")
        page.goto(url, wait_until="domcontentloaded")
        fill_form(page, profile, log)

        if smart:
            ptitle = page.title()
            if "@" in ptitle:
                role, _, company = ptitle.rpartition("@")
            elif " at " in ptitle:
                role, _, company = ptitle.rpartition(" at ")
            else:
                company, role = ptitle, ""
            company = company.strip() or ptitle
            fill_custom(page, profile, company, role.strip(), log)
            fill_clicks(page, profile, company, log)
            if "greenhouse.io" in page.url:
                fill_gh_selects(page, profile, company, log)

        if test:
            shot = str(HERE / "autofill_test.png")
            page.screenshot(path=shot, full_page=True)
            log("\n==> READBACK (what's actually in the form):")
            for css, name in (('input[type="email"]', "email"),
                              ('input[type="tel"]', "phone")):
                el = page.locator(css).first
                if el.count():
                    log(f"    {name} = {el.input_value()!r}")
            log(f"    screenshot -> {shot}")
            browser.close()
            return

        log("\n==> DONE. NOT submitting. Review, answer custom questions, submit yourself.")
        input("    Press Enter here to close this browser window...")
        browser.close()


def autofill_batch(n: int, category: str, profile: dict, test: bool = False,
                   smart: bool = False):
    """Open the next N fillable Ashby roles, each in its own tab, fill them all,
    and leave every tab open for review. Nothing is submitted."""
    store = Store(str(HERE / "funnel.db"))
    fillable = ("ashbyhq.com", "greenhouse.io")
    jobs = [j for j in store.list(status="new")
            if any(f in j.url.lower() for f in fillable) and not j.is_grad
            and (not category or j.category == category)][:n]
    if not jobs:
        sys.exit("No fillable Ashby/Greenhouse roles left in the queue (status=new).")

    print(f"==> Batch: filling {len(jobs)} role(s) into {len(jobs)} tabs.\n")
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=test)
        ctx = browser.new_context()
        for i, job in enumerate(jobs, 1):
            print(f"=== [{i}/{len(jobs)}] {job.company} — {job.title} ===")
            page = ctx.new_page()
            try:
                page.goto(job.url.split("?")[0], wait_until="domcontentloaded")
                fill_form(page, profile, print)
                if smart:
                    fill_custom(page, profile, job.company, job.title, print)
                    fill_clicks(page, profile, job.company, print)
                    if "greenhouse.io" in page.url:
                        fill_gh_selects(page, profile, job.company, print)
                if not test:
                    store.set_status(job.key, "drafted",
                                     note="autofilled standard fields; review + submit")
            except Exception as e:
                print(f"  ! failed to fill this one: {e}")
            print()
        print(f"==> {len(jobs)} tabs filled. NONE submitted.")
        if test:
            browser.close()
            return
        print("    Go tab by tab: review fields, answer custom/Yes-No questions,")
        print("    check consent, then Submit each yourself.")
        input("    Press Enter here to close ALL tabs...")
        browser.close()


if __name__ == "__main__":
    load_env()
    argv = sys.argv[1:]
    test = "--test" in argv
    smart = "--smart" in argv
    argv = [a for a in argv if a not in ("--test", "--smart")]

    if "--batch" in argv:
        i = argv.index("--batch")
        rest = argv[i + 1:]
        n = next((int(a) for a in rest if a.isdigit()), 5)
        category = next((a for a in rest if a in ("swe", "data-ml", "pm", "other")), None)
        autofill_batch(n, category, load_profile(), test=test, smart=smart)
    elif argv:
        autofill(resolve_url(argv[0]), load_profile(), test=test, smart=smart)
    else:
        sys.exit("usage:\n"
                 "  python autofill.py <job-key | url>          # one form (standard fields)\n"
                 "  python autofill.py --batch [N] [category]   # N Ashby forms in N tabs\n"
                 "  add --smart to also answer custom questions via Claude (needs API key)\n"
                 "  add --test to run headless and screenshot")

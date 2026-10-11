# ApplyFlow

A personal, **assisted** job-application funnel. Point it at a GitHub job-list
repo (the popular SimplifyJobs / Pitt-CSC / vanshb03 internship lists, or your
own), and it will:

1. **sync** every role out of the repo's table (markdown *or* HTML) into a local
   SQLite tracker, de-duplicating so re-syncs never lose your progress;
2. **track** each role through a funnel: `new -> drafted -> applied ->
   interview -> offer` (plus `rejected` / `skipped`);
3. **draft** a tailored cover-letter / intro per role from your `profile.yaml`;
4. **open** each application link in your browser so you review and hit Submit.

## Why "assisted" and not "auto-submit"

It never submits an application for you, by design:

- Most application portals (Greenhouse, Lever, **Workday**, LinkedIn) prohibit
  automated submission in their ToS, and Workday actively blocks bots.
- Recruiters can spot mass-generated applications; it hurts more than it helps.
- A bot silently submitting wrong or blank answers to hundreds of companies is a
  worse outcome than 20 solid, human-checked applications.

So this tool does the tedious 90% (find, dedupe, track, draft, open pre-filled)
and leaves you the 10% that has to stay human: the final read and click.

## Setup

```bash
git clone https://github.com/sidnags07-maker/job-apply-assistant.git
cd job-apply-assistant
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

Copy the example configs and fill them in (all three are git-ignored, so your
details and keys never get committed):

```bash
cp profile.example.yaml profile.yaml                          # background used in drafts
cp application_profile.example.yaml application_profile.yaml  # fields the autofiller types
cp .env.example .env                                          # optional LLM keys
```

Then add the GitHub URL(s) of the job-list repo(s) you follow to
**`config.yaml`** under `repos:`.

Autofill additionally needs a browser:

```bash
pip install playwright && playwright install chromium
```

## Use

```bash
python apply.py sync                       # pull the repos in config.yaml
python apply.py sync <github-repo-url>      # or one specific repo

python apply.py list --status new --limit 20
python apply.py list --search stripe        # filter by company/title
python apply.py list --category swe          # swe | data-ml | pm | other
python apply.py list --include-grad          # show 🎓/PhD/MBA roles (hidden by default)

python apply.py next                         # next new role, SWE > data-ml > PM > other
python apply.py next --category data-ml      # next role from one category only
python apply.py draft <key>                  # (re)generate a draft
python apply.py open  <key>                  # just open the link

python apply.py mark <key> applied --note "referred by Priya"
python apply.py mark <key> skipped --note "needs 3+ yrs"
python apply.py stats                        # funnel counts
```

`<key>` is the short id shown in `list` (prefix-matching, so the first few chars
are enough).

## Better drafts with an LLM (optional)

Template drafts work offline with zero cost. To have a model tailor each letter to
the specific company/role, set an API key (put it in a git-ignored `.env` file
here as `KEY=value`, or export it):

**Gemini (free tier):**
```bash
pip install google-genai
echo 'GEMINI_API_KEY=...' >> .env      # from aistudio.google.com/apikey
```

**Claude (paid, highest quality):**
```bash
pip install anthropic
echo 'ANTHROPIC_API_KEY=sk-ant-...' >> .env
```

The **Claude** backend also runs a web search per draft, so each letter is
grounded in what the company actually builds instead of generic filler (it will
not invent facts; if it finds nothing specific it keeps the company mention
modest). Fill in `github`/`linkedin` in `profile.yaml` and the model can cite them.

`draft` / `next` then upgrade automatically. `draft_provider` in `config.yaml`
(or `--provider gemini|claude|template|auto` per run) controls which is used;
`auto` prefers free Gemini, then Claude, then the template. Any failure falls back
to the template so it never breaks.

Two things to know about Gemini free tier: Google **trains on your inputs** (your
`profile.yaml` is just resume highlights, so low-stakes, but not private), and
**rate limits** are low (a few requests/min). Draft only the jobs you'll actually
apply to. Models: `gemini-2.5-flash` / `claude-sonnet-5` (edit in `draft.py` if
your key lacks access).

## Layout

```
apply.py                 CLI entry point (sync/list/next/draft/open/mark/stats)
config.yaml              repos to follow + db/drafts paths
profile.yaml             your background, the raw material for drafts (git-ignored)
autofill.py              Playwright autofiller for Ashby forms (never submits)
assistant/
  models.py              Job dataclass + funnel statuses + stable dedupe key
  parse.py               fetch README, parse markdown AND HTML job tables
  store.py               SQLite tracker with status-preserving upserts
  draft.py               template drafts, optional Claude-tailored drafts
drafts/                  generated per-job letters (git-ignored by default)
funnel.db                your tracker (git-ignored by default)
```

## License

MIT. See [LICENSE](LICENSE).

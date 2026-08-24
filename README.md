# Covidence Screener — Claude Edition

Automates title/abstract screening on [Covidence](https://www.covidence.org/) using Playwright for
browser automation and **Claude** (Anthropic) for classification, with a built-in
random human spot-check so you can keep an eye on the AI without reviewing every article.

This is a fork of [Aisha630/covidence_screening](https://github.com/Aisha630/covidence_screening),
adapted to: (1) run on Claude instead of Gemini, (2) work for *any* Covidence
review out of the box instead of one hardcoded review, and (3) pause for a
random sample of decisions so you can confirm the AI is on track.

---

## What it does

1. Logs into Covidence with credentials from `.env`
2. Opens your review's screening queue
3. For each study: reads the title/abstract, checks if it's already been
   processed (skips re-classifying), otherwise sends it to Claude along with
   your protocol
4. Claude returns `Include` / `Exclude` / `Maybe` + a justification
5. **Randomly, for a configurable fraction of articles, the script pauses and
   shows you the AI's decision before submitting the vote** — you can
   approve, override, or skip
6. Casts the vote on Covidence (`Yes` / `No` / `Maybe`) and logs everything to
   `processed_articles.csv` (and spot-checks to `spot_check_log.csv`)

---

## Setup (one-time)

**1. Install [uv](https://docs.astral.sh/uv/)** if you don't have it:
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

**2. Install dependencies and the Playwright browser:**
```bash
uv sync
uv run playwright install chromium
```

**3. Create your `.env` file** (copy `.env.sample` → `.env` and fill in):
```env
COVID_ID=your_covidence_email
COVID_PASSWORD=your_covidence_password
ANTHROPIC_API_KEY=sk-ant-...
COVIDENCE_REVIEW_URL=https://app.covidence.org/reviews/<your_review_id>/review_studies/screen?filter=vote_required_from
REVIEW_TITLE=Efficacy of X for Y in Z population
```
To get `COVIDENCE_REVIEW_URL`: open your review in a browser, go to the
screening tab filtered to studies awaiting your vote, and copy that exact URL.

Get an Anthropic API key at https://console.anthropic.com/ (Settings → API Keys).
You can comma-separate multiple keys (`key1,key2`) if you want to round-robin
across them for rate limits.

**4. Write your protocol.** Open `protocol.txt` and replace the placeholder
with your review's actual inclusion/exclusion criteria (e.g. copy from your
PROSPERO registration or protocol document). The more precise and numbered
your criteria, the more consistent Claude's decisions will be.

---

## Running it

```bash
uv run main.py
```

Useful flags:

| Flag | Default | What it does |
|---|---|---|
| `--articles N` | 3700 | Stop after this many total processed articles |
| `--output FILE` | `processed_articles.csv` | Where results are logged |
| `--headless` | off | Hide the browser window (still shows console prompts) |
| `--spot-check-rate R` | 0.1 | Fraction of Include/Exclude decisions (0.0–1.0) that pause for your review. Every `Maybe` is *always* spot-checked regardless of this. |

Examples:
```bash
# Spot-check ~25% of Include/Exclude decisions instead of the default 10%
# (Maybe is always checked no matter what)
uv run main.py --spot-check-rate 0.25

# Turn off the RANDOM sample (Maybe decisions still always pause)
uv run main.py --spot-check-rate 0 --headless

# Only process the next 50 articles
uv run main.py --articles 50
```

### How the spot-check works

A study gets spot-checked if Claude classified it as `Maybe` (always — it's
already the least-confident call) OR at random for `Include`/`Exclude`
(probability = `--spot-check-rate` per article). When that happens, the
script prints the title, abstract, source info, and Claude's proposed
decision + justification, then waits for you at the terminal:

```
[Enter]=approve  i=Include  e=Exclude  m=Maybe  f=flag for your own manual follow-up (still votes as shown above):
```

- **Enter** — accept Claude's decision as-is
- **i / e / m** — override with your own decision (only logged as an
  "override" if it's actually different from what Claude said — typing the
  same letter Claude already landed on just counts as a confirmation)
- **f** — flag this one for you to revisit yourself later in the Covidence
  UI (note: it still casts Claude's original vote now — Covidence's queue
  always shows the next study still needing *your* vote, so there's no way
  for the script to truly leave one un-voted without getting stuck showing
  you that same article forever)

Every spot-check is appended to `spot_check_log.csv` (columns: title,
ai_decision, human_action, final_decision) so you have an audit trail of how
often you actually agreed with the AI vs. overrode it — useful for deciding
whether to raise or lower `--spot-check-rate` as you go, and as documentation
of your screening process.

### Learning from your corrections

When you override a decision (`i`/`e`/`m`), the script asks a follow-up:
*"Why?"* — one line, optional but worth typing. If you answer, that
correction (what Claude got wrong, what it should have been, and your
reason) is saved to `learned_corrections.jsonl` and automatically included
in the prompt for every article classified afterward — including the rest
of this run and any future runs, since the file persists.

This is in-context learning, not real fine-tuning — there's no training
step, Claude is just shown its own past mistakes on this review each time
it classifies a new article, so it doesn't repeat the same category of
error (e.g. an animal study described in human-clinical language). To keep
the prompt from growing forever, only the most recent 25 corrections are
included (configurable via `MAX_LEARNED_CORRECTIONS` in `.env`). Skipping
the "why" prompt (just hitting Enter) still logs the override to
`spot_check_log.csv` for the audit trail, it just won't teach Claude
anything from that one.

Since this pauses on the console (not the browser), it works whether or not
you pass `--headless` — just don't walk away mid-run if spot-check is > 0,
or the script will sit waiting for your input.

---

## Notes / gotchas

* **Duplicate protection:** articles already in your output CSV (by exact
  title match) are re-voted the same way without calling Claude again.
* **If extraction fails** (missing title/abstract on the page): votes `Maybe`
  automatically and logs why.
* **Retries:** browser and API calls retry automatically (up to 3 attempts,
  exponential backoff).
* **Rate limiting:** capped at 100 Claude calls/minute.
* **Switching models:** change `CLAUDE_MODEL` in `.env` to use a different
  Claude model (e.g. a cheaper/faster one for a first pass, or a stronger one
  for edge cases).
* **Academic/research use only** — see `LICENSE` (CC BY-NC 4.0), same as the
  original project.

## Troubleshooting

* `RuntimeError: COVIDENCE_REVIEW_URL is not set` — you haven't filled in `.env` yet.
* `RuntimeError: ANTHROPIC_API_KEY is not set` — same, add your key.
* `Protocol file 'protocol.txt' not found` — make sure `protocol.txt` exists
  in the same folder as `main.py` and isn't still just the placeholder text.
* Login fails — double check `COVID_ID`/`COVID_PASSWORD`, and that Covidence
  doesn't require 2FA on this account (if it does, log in manually once in a
  non-headless run to clear any prompts, then re-run).

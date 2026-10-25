# Second Read

Daily literature surveillance with checked agents. The Python package is called `litwatch`. It searches PubMed for newly added papers on one topic, screens them, extracts the key facts, writes a short summary, checks every step, estimates what it missed, and writes a report for a human reviewer.

The rule throughout: **a model proposes, code decides.** Models answer narrow questions and must point to a numbered sentence in the abstract. The decisions (include or exclude, accept or send to a reviewer) are made in code, from those answers and from checks that cannot hallucinate.

## The six stages

| Stage | Who | What happens |
|---|---|---|
| Watch | code | Two PubMed searches built from different terms, on papers added in the chosen window |
| Screen | writer model | Each inclusion criterion answered yes, no or unclear, with the sentence it relied on. Code decides: any required "no" excludes; "unclear" includes, so nothing relevant is dropped |
| Extract | writer model | Fixed fields (variant, context, drug, setting, study type, patients, finding type, key finding), each with a word-for-word quote |
| Summarise | writer model | Two sentences written from the extracted fields only. The model never sees the abstract |
| Verify | code, then checker model | Code: every quote is in the abstract, every number is in the source, key tokens of each value are in its quote, category fields hold an allowed value, every number in the summary is in the record. Checker, from a different model family: supported, contradicted or not in the abstract, for every field and summary sentence, plus findings the record left out |
| Route | code | Accepted only if every check passed. Contradictions, failed checks, missing abstracts and high-impact findings go to a reviewer |

**What it may have missed.** The overlap between the two searches gives a capture–recapture estimate (Chapman) of how many relevant papers neither search found. Papers only the second search found are listed as misses of the main search. A reproducible random sample of excluded papers is listed for a person to check, which measures wrong exclusions.

**Audit trail.** Every run stores the models, prompt versions, topic definition hash, both queries, a SHA-256 of every abstract read, and every model answer, in `run.json`.

## Requirements

- Python 3.10 or newer. No packages to install: standard library only.
- An API key for the writer model and one for the checker model, from **different providers**. If both come from the same family, their mistakes are more likely to line up and the second check is worth less.
- Optional: an NCBI API key, which raises the PubMed rate limit from 3 to 10 requests a second.

## Run it

```bash
export ANTHROPIC_API_KEY=...
export OPENAI_API_KEY=...
export NCBI_EMAIL=you@example.com        # NCBI asks tools to identify themselves
# export NCBI_API_KEY=...                # optional

python -m litwatch run --topic topics/egfr_osimertinib_resistance.json --days 30
```

The command prints the path of `report.html`. Open it in a browser. `run.json` next to it holds the full record.

Useful options:

- `--days 1` is the daily setting; `--days 30` gives a fuller first report.
- `--limit 5` processes only five papers, for a cheap first try.
- `--writer anthropic:<model>` and `--checker openai:<model>` choose the models. The defaults are set in `litwatch/__main__.py`; check that those model names are current for your accounts before a real run.
- `python -m litwatch render runs/<run>/run.json` rebuilds the page from a saved run.

### Free options

Any provider below works for `--writer` and `--checker`. Keep the two from different model families.

| Provider | Spec | Key | Notes |
|---|---|---|---|
| Ollama, on your computer | `ollama:<model>` | none | Install Ollama, then `ollama pull <model>`. Free and private, but small local models make more extraction mistakes, so more papers go to a reviewer. |
| Google Gemini, free tier | `gemini:<model>` | `GEMINI_API_KEY` from Google AI Studio | Daily request limits on the free tier; fine for daily runs, may run out on a 30-day first run. |
| Groq, free plan | `groq:<model>` | `GROQ_API_KEY` | Hosts open models such as Llama and Qwen; limits per minute and per day. |

Example with no paid key at all:

```bash
export GEMINI_API_KEY=...
python -m litwatch run --topic topics/egfr_osimertinib_resistance.json --days 7 \
  --writer gemini:<gemini model> --checker ollama:<local model>
```

Check each provider's current model names and limits in its console before a run.

### Daily run on GitHub, published on Vercel

`.github/workflows/daily.yml` runs the pipeline every day, copies the report into `site/` (with a dated copy in `site/archive/`), and commits it. Vercel, connected to this repository, publishes `site/` on every commit. Keys live only in GitHub secrets.

One-time setup in the GitHub repository, under Settings, Secrets and variables, Actions:

- Secret: `GROQ_API_KEY` is all the free setup needs. Other providers use `GEMINI_API_KEY`, `ANTHROPIC_API_KEY` or `OPENAI_API_KEY`; `NCBI_API_KEY` is optional.
- Variables (optional): `WRITER_MODEL` and `CHECKER_MODEL` to change models without touching code, and `NCBI_EMAIL`. Defaults, both free on Groq and from different model families: `groq:qwen/qwen3.8-27b` (Alibaba) writes, `groq:openai/gpt-oss-120b` (OpenAI) checks. The free tier allows about 200,000 tokens a day per model: enough for daily runs, while a 30-day first run may need two days. A run that hits the limit stops cleanly, and the next run continues from `cache/`, where the writer's and the checker's work are kept separately.

Start a first run by hand from the Actions tab, "Daily run", "Run workflow", with 30 days for a fuller first report.

To run it every day on your own computer instead, schedule the command with cron, launchd or Task Scheduler using `--days 1`.

## Add a topic

Copy `topics/egfr_osimertinib_resistance.json` and change:

- `primary_query` and `secondary_query`: two PubMed queries that aim at the same papers with **different** terms. The more independent they are, the better the missed-paper estimate.
- `criteria`: inclusion criteria; `required: true` ones exclude on a "no".
- `fields`: what to extract. Use `"kind": "category"` with `values` for a field that is a judgement rather than a quote.
- `high_impact`: field values that always go to a reviewer.
- `synonyms`: names the code checks should treat as the same (drug code names, abbreviations).

## Tests and layout preview

```bash
python -m unittest discover -s tests
python tests/preview.py --days 30
```

The preview runs on real PubMed papers with an offline keyword stub in place of the models, so you can look at the report without API keys. Its page carries a banner saying so; its screening and extraction are not real results.

## Limits, stated plainly

- It reads titles and abstracts only. Full text is freely usable only for the PubMed Central open-access subset; anything else is a licensing question.
- The missed-paper estimate assumes the two searches find papers independently. Some dependence always remains, which makes the estimate a lower bound.
- The checker model reduces errors; it does not remove them. The measured answer to "how good is it" comes from comparing runs against expert decisions on the same papers.

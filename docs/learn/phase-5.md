# Phase 5: Evaluation, the part that sells it

Phases 1-4 built a memory system. Phase 5 answers the only question an interviewer really cares
about: **is it any good, and how do you know?**

```
 questions ──► [ full context ]──┐
 (benchmark)   [ naive RAG    ]──┼─► same answer model ─► judge ─► right / wrong ─► statistics ─► README table
               [ this system  ]──┘
```

Code: `eval/lme.py` (runs the systems), `eval/locomo.py`, `eval/judge.py`, `eval/llm_cache.py`,
`eval/stats.py`, `eval/report.py`, `eval/agreement.py`. Tests: `tests/test_stats.py`.
Decisions: ADR 0008, 0015, 0017.

**Teaching status:** written while building, before any teaching. Teach one section at a time.

---

## 5.1 Why a benchmark at all

**Picture it.** A driving test. You can tell your family you drive well, but the examiner has a
fixed route, a fixed checklist, and gives the same test to everyone. That is what makes a pass mean
something.

**Plainly.** A benchmark is a fixed set of questions with known right answers. We run our memory
system on them and count how many it gets right. Anyone can run the same questions and check.

**Show it.** One LongMemEval question (real, from our smoke test): *"How much did I earn at the
Downtown Farmers Market on my most recent visit?"* The chat history has three market visits spread
over months ($220, $350, $420). The right answer is $420. A system that remembers the *old* number
fails. That is the "knowledge update" skill.

**The real version.**
- **LongMemEval** (Wu et al., 2024): 500 questions about long chat histories. The `_s` split gives
  each question about 48 sessions (roughly 115k tokens). Five abilities: information extraction,
  multi-session reasoning, knowledge updates, temporal reasoning, and abstention (knowing when
  the answer is *not* in the history).
- **LoCoMo** (Maharana et al., 2024): 10 very long two-person conversations, about 200 questions each.
  Categories: single-hop, multi-hop, temporal, open-domain (and adversarial, which we drop,
  ADR 0017).

**In our code.** `eval/lme.py` loads the questions, `eval/locomo.py` converts LoCoMo into the same
shape so one harness runs both.

**Interview line.** "I evaluate on two public benchmarks and report per-category results, because
a single headline number hides which skill is weak."

<details><summary>Check: why is "abstention" a skill worth testing?</summary>
A memory system that always answers will make things up when the fact was never stored. Saying
"I don't know" correctly is part of being trustworthy.
</details>

---

## 5.2 Three systems, one fair fight

**Picture it.** Three students sit the same exam with the same pen. One may bring the whole
textbook (`full`), one a stack of highlighted pages found by keyword (`rag`), one a notebook
they wrote while the course ran (`ours`).

**Plainly.**
- **full:** paste the entire chat history into the prompt. Probably the most accurate, and the
  most expensive; it is our cost ceiling.
- **rag** ("retrieval-augmented generation"): cut the history into chunks, find the chunks most
  similar to the question, paste only those.
- **ours:** run every message through the write path (facts, updates, history), then `recall`.

**The real version (fairness rules, ADR 0015).** Same answer model for all three. `rag` and `ours`
get the *same* context budget (2000 tokens); `full` is unbudgeted. RAG's one-time indexing cost
is excluded from its read latency, because it is a write-time cost, but our read latency *includes*
the LLM query rewrite, because that is a real cost of our design. We take the harder number for
ourselves on purpose.

**In our code.** `sys_full`, `sys_rag`, `sys_ours` in `eval/lme.py`; `RUNNERS` maps names to them.

**Why it matters.** If you give your system more context than the baseline, you have measured the
budget, not the design.

**Interview line.** "Baselines get the same model and budget; where costs were ambiguous I charged
them to my system."

<details><summary>Check: why does full context cost more but not always win?</summary>
Long prompts cost money per token, add latency, and models get worse at finding a needle in a
very long haystack ("lost in the middle").
</details>

---

## 5.3 LLM-as-judge, and why you must check the judge

**Picture it.** Marking essays. A teacher (the judge) reads the model's answer and the gold answer
and says "yes, matches" or "no". It scales better than humans, but teachers have habits.

**Plainly.** Free-text answers can't be matched with `==` ("$420" vs "You earned 420 dollars").
So another LLM reads question, gold answer and response, and replies yes or no. The benchmark
supplies the judge prompts; we copied them verbatim (`eval/judge.py`).

**The real version: known pitfalls** (Zheng et al., 2023, "Judging LLM-as-a-Judge"):
- **Self-preference:** a model tends to prefer text that looks like its own. Our judge is a Claude
  model grading Claude answers. The official judge is GPT-4o; we have no OpenAI key, so every
  result is stamped `judge_is_official: false` and is only comparable *within* our own runs.
- **Prompt sensitivity:** small wording changes move scores. That is why we keep the benchmark's
  prompt and only change the model.
- **Leniency:** a "generous" judge inflates everything.

**The fix: measure the judge.** Hand-label 50 answers yourself *without seeing the judge's verdict*,
then compute **Cohen's kappa** (κ): agreement beyond chance. Two raters who both say "yes" 90% of
the time will agree a lot by luck; κ subtracts that luck.

**Show it.** 8 answers. Human says yes to 4. Judge says yes to 3. They disagree on 1 of 8.
- observed agreement = 7/8 = 0.875
- chance agreement = (0.5 × 0.375) + (0.5 × 0.625) = 0.5
- κ = (0.875 − 0.5) / (1 − 0.5) = **0.75**
(That exact case is a test: `test_kappa_by_hand`.) Rough scale (Landis and Koch): 0.61-0.80
substantial, above 0.80 almost perfect.

**In our code.** `python -m eval.agreement sample <run> --n 50` writes a CSV with the judge's verdict
hidden; you fill the `human` column; `python -m eval.agreement score` writes
`eval/results/judge-agreement.json`, and the README shows it.

**Interview line.** "I didn't trust the judge, I measured it: 50 blind hand labels, κ reported
next to the results."

<details><summary>Check: why hide the judge's verdict from the human labeller?</summary>
Seeing it anchors you: you drift toward agreeing, which inflates κ.
</details>

---

## 5.4 Is the difference real? Paired tests

**Picture it.** Two runners race the same 20 hurdles. Runner B clears 16, A clears 10. Is B really
better, or did B just get lucky on a small sample?

**Plainly.** With few questions, a gap can be luck. We use three tools:

1. **Paired** comparison: both systems answer the *same* questions, so we compare question by
   question. Hard questions hurt both, so difficulty cancels out.
2. **Bootstrap confidence interval:** pretend the question set is the whole world. Resample it
   with replacement 10,000 times, recompute the accuracy gap each time, and take the middle 95% of
   those gaps. If the interval contains 0, we can't claim a difference.
3. **McNemar's exact test:** only the *disagreements* matter. Say A and B differ on 6 questions and
   B wins all 6. If they were equally good, each disagreement is a coin flip, so P(6 of 6 to B) =
   (1/2)^6; two-sided, 2 × 1/64 = 0.03.
4. **Holm correction:** if you run many comparisons, some will look significant by luck. Holm sorts
   the p-values and makes the bar stricter, step by step, so the overall false-alarm rate stays at 5%.

**Show it.** `test_paired_counts_only_the_disagreements`: A right on 10 of 20, B right on those 10
and 6 more → Δ = +30 points, `only_b` = 6, p = 0.03125 (= 2/64).

**The real version.** ADR 0008 sizes this: at 500 questions and 10% disagreement the smallest
detectable gap is about 4 points. A 1-2 point "win" is noise and the report says so in words.

**In our code.** `eval/stats.py`: `wilson` (interval for one accuracy), `paired`, `mcnemar_exact`,
`holm`, `kappa`. `eval/report.py` turns them into the table.

**Interview line.** "Comparisons are paired, with bootstrap CIs, McNemar, and Holm; small gaps are
reported as noise."

<details><summary>Check: A and B disagree on 6 questions, and each wins 3 of them. What is the McNemar p?</summary>
1.0. A 3-3 split is exactly what equal systems would give, so there is no evidence of a difference
(`mcnemar_exact(3, 3) == 1.0`).
</details>

---

## 5.5 Ablations: what is each piece worth?

**Picture it.** A car with a turbo, a spoiler and better tyres. To learn what each does, remove one
at a time and re-time the lap.

**Plainly.** An *ablation* switches one component off and re-runs. If accuracy barely moves, that
component isn't earning its complexity.

**Our switches (flags on `python -m eval.lme`):**

| Flag | What it removes |
|---|---|
| `--retriever vector` / `text` | one half of hybrid search |
| `--no-history` | bi-temporal history (a replaced fact is deleted, not closed) |
| `--no-rerank` | importance and recency (plain RRF order) |

Reads don't change the stored data, so retriever and rerank ablations reuse the same ingested
memories; only `--no-history` needs its own copy (`user_suffix`).

**In our code.** `Env.ours_label` names each variant (`ours (vector-only)`), and
`eval.report a b c` merges runs into one table with every variant compared against `ours`.

**Interview line.** "An ablation table matters more than a headline number, because it shows which
parts of the design carry weight."

<details><summary>Check: if removing rerank changes nothing, what should you do?</summary>
Either remove it (less code) or find out why it's inert. Don't keep it on the strength of the plan.
</details>

---

## 5.6 Reproducibility: cache every model call

**Plainly.** Calling an LLM is slow, costs money, and can answer differently twice. `CachedLLM`
remembers every answer, keyed by a hash of (model, schema, prompt) in a SQLite file. A rerun, or an
ablation that repeats a call, costs nothing and gives the identical result.

**The real version.** The same cache gates concurrent calls (`max_in_flight`), so a big run can't hammer
the API. Ingestion is keyed by a hash of the models and prompts (`ingest_key`), so changing the
extraction prompt automatically invalidates old memories.

**Interview line.** "Every call is content-addressed, so results are reproducible and an ablation costs
only what it changes."

<details><summary>Check: why include the model name in the cache key?</summary>
A different model gives a different answer to the same prompt; sharing a key would return the
wrong model's answer.
</details>

---

## 5.7 Try it yourself

1. `uv run --env-file .env python -m pytest tests/test_stats.py -q` → `5 passed`.
2. Run the small pilot: `uv run --env-file .env python -m eval.lme --n 12 --name pilot-12 --concurrency 2`.
   (Costs a little money; takes a while; keep the machine idle.)
3. `uv run python -m eval.report pilot-12` and read the tables.
4. Ablate: rerun with `--retriever vector --name abl-vector`, then
   `uv run python -m eval.report pilot-12 abl-vector`.
5. Label the judge: `python -m eval.agreement sample pilot-12`, fill the CSV, run `score`.

**Reading.** LongMemEval paper (Wu et al., 2024); LoCoMo (Maharana et al., 2024); "Judging
LLM-as-a-Judge with MT-Bench and Chatbot Arena" (Zheng et al., 2023); Efron, *Bootstrap Methods*
(1979); Landis and Koch (1977) for the κ scale.

# 0008. Offline paired evaluation first, in-house online A/B

**Status:** Accepted, 2026-10-16

## Context
"A/B testing" means two different things here:
1. **Offline:** compare variants on a fixed benchmark. This is where the statistically
   meaningful claims come from.
2. **Online:** split live traffic between variants. A resume project won't have enough
   users for significance, but the plumbing is production practice worth showing.

## Offline decision
- Every variant runs on the **same questions**, so comparisons are *paired*. Pairing
  removes question difficulty as a source of noise.
- Report **Δ accuracy with a 95% paired bootstrap CI** (10k resamples of question IDs),
  plus an **exact McNemar test** on the discordant pairs (A right / B wrong vs the
  reverse).
- **Power check at n=500:** the smallest detectable difference is about 2.8·√(p_disc/n),
  where p_disc is the share of questions the two variants disagree on. At 10% discordance
  that is about 4 points, so 1–2 point "wins" are noise and get reported as such.
- With several ablations, apply the **Holm correction**.
- **Judge hygiene:** temperature 0, the judge model and prompt version pinned in every
  results file. Hand-label 50 answers and report judge–human agreement (Cohen's κ).
- **Cost control:** ablations run on a stratified 150-question dev sample. Only the final
  configs run on all 500 questions.

## Online decision
- Assignment: `sha256(f"{experiment}:{user_id}") % 100 < split`. It is deterministic and
  sticky, and needs no stored state or flag service.
- Every read logs `(experiment, variant, user, request_id)` to `experiment_exposures`.
  When the agent cites or uses a returned memory, that is logged as an outcome.
- **Team-draft interleaving** (Radlinski et al., CIKM 2008) for ranking variants: both
  variants contribute to one merged list, and the variant whose memory gets used wins
  that query. It needs 10–100× fewer users than a split test.
- The README states plainly that the online numbers come from synthetic or self traffic
  and are underpowered. **No significance is claimed online.**

## Rejected
GrowthBook, Statsig and PostHog all work, but they add a service to run without adding
credibility at this scale. The in-house version is about 30 lines plus one table. Langfuse
is optional for traces.

**In an interview:** "Statistical claims come from paired offline tests with CIs and
McNemar, sized with a power calculation. Online I built hash-based assignment and
interleaving, and I'm explicit that my traffic can't show significance."

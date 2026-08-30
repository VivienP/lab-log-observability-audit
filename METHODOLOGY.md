# Methodology

Detailed method for the audit summarised in [`README.md`](README.md). Every rule
here is implemented in `src/lab_log_audit/` and exercised by `tests/`.

## 1. Evidence classes

The audit keeps three records conceptually separate and never collapses them:

1. **Requested software action** — a command recorded by the controlling software.
2. **Controller or device readback** — a value reported back by the equipment, *when
   its semantic origin is established*. The Flex-Cat source does not establish that
   class for `actualVolume`.
3. **Independent physical evidence** — an observation from a distinct physical
   channel, sufficient to support a claim about the physical effect.

The same separation applies to recoveries. `RecoveryLabel` (an expert annotation),
`RecoveryEvidence` (log activity near it), and `RecoveryOutcome` (the plant actually
returning to normal) are three different things. The coverage metric measures the
second one only.

## 2. Chemspeed operation identity and pairing

- Operation identity is `(application_epoch, operationid)`, and applies **only** to
  events with `type="operation"`. `application_epoch` increments on each
  `<start type="application">`, because `operationid` restarts within a file.
- Start and end counts are replayed chronologically. Pairing is `clean` only when an
  identity has exactly one start and exactly one end. Every other combination is
  classified explicitly (`missing_start`, `missing_end`, `duplicate_start`,
  `duplicate_end`, `count_mismatch`) rather than dropped.
- The eventlog is decoded as Windows-1252. A malformed XML fragment or a line that is
  not three tab-separated fields raises `InputFormatError`; nothing is silently skipped.

## 3. Transfer endpoints and the `actualVolume` comparison

- Endpoint `id` is **not** unique inside a transfer in the source data, so `src` and
  `dest` endpoints are paired **positionally** between the start and end elements. A
  change in endpoint cardinality between start and end is an error, not a fallback.
- Requested `volume` and reported `actualVolume` are compared as `Decimal`, never as
  binary floats, so `0.1` means the decimal `0.1`.
- Endpoints missing either value are excluded from the 60/60 comparison and counted
  separately as `skipped_incomplete_endpoints` (zero in the pinned release).
- Exact equality of the two fields does **not** establish that `actualVolume` is an
  independent physical measurement, and it does not establish that the field is false.
  If the value is a controller or pump readback, its name alone does not tell a
  downstream consumer which evidentiary class it belongs to. The result is emitted
  with `evidence_class = reported_value_semantics_unverified` for that reason.

## 4. Recovery labels and their anchors

- Annotations are deduplicated by `(experiment, class, anomaly_id)`. Entries with no
  class, or with no `hasRecoveryAction`, are not recoveries.
- The anchor is the **unique** `PerturbationMode.hasEnd`, with the unique
  `hasEnd` of the anomaly as an explicit fallback. Ambiguity — two distinct
  perturbation ends, or two distinct recovery actions for one identity — raises rather
  than picking one.
- Source metadata contains 81 labelled recoveries. Two belong to one experiment for
  which the pinned release ships no operation-log file, so they are excluded by name in
  `results/metrics.json`. The analysed denominator is 79.

## 5. Temporal windows and the coverage definition

- Timestamps are matched as **time of day** (`HH:MM:SS`). Rows whose timestamp does
  not parse in that format are never matched; the count of such rows is reported.
- Midnight rollover is **not** inferred. No analysed recovery window crosses midnight
  in the pinned release.
- A recovery is `matched` when at least one operation-log row of the same experiment
  has a parseable time inside the inclusive window `[anchor - pre, anchor + post]`.
- This is an **observability/activity proxy**. It is not evidence that the labelled
  physical or operator recovery itself was observed, and a silent window is not
  evidence that no intervention occurred.

## 6. Window sensitivity

The predeclared windows are `[-60 s, +120 s]`, `±300 s`, and `±600 s`
(`results/window_sensitivity.csv`). Wider windows can capture unrelated plant or UI
activity, so the table measures robustness to window width and nothing else. Section 7
shows that the apparent gain from widening the window is entirely explained by that
effect.

## 7. Background / null comparison

The coverage metric alone cannot say whether the activity it counts is *unusual*. The
null answers the separate question: **is log activity concentrated around recovery
labels more than around ordinary instants of the same log?**

### 7.1 Construction

For each analysed recovery the labelled anchor is replaced by a random anchor while
everything else is held fixed:

- the same experiment, and therefore that experiment's own event density — timestamps
  are **never pooled across experiments**;
- the same operation-log event structure, unmodified;
- the same temporal window;
- anchors drawn uniformly over integer seconds from that experiment's **observable log
  interval**, defined as `[first parseable event time, last parseable event time]`.

The primary anchor domain is `interior`: `[low + pre, high - post]`, so the whole
window fits inside the observable interval. That is the like-for-like domain, because
**all 74 comparable real anchors satisfy exactly that condition** in the pinned
release. `full_interval` is the relaxed alternative and is reported as a sensitivity
variant.

Seed `20260830`, 10,000 iterations, `random.Random` (stdlib Mersenne Twister). Both
are module constants in `src/lab_log_audit/background.py`.

### 7.2 Which recoveries enter the comparison

5 of the 79 included recoveries are anchored **outside** their own experiment's
observable log interval. No window of any width can ever match them, because the
operation log does not cover that instant at all. They are *unanswerable* for the
coverage question rather than negative answers to it, so they are excluded from the
background comparison and reported by name in `results/metrics.json`
(`inclusion.anchored_outside_source_records`) and row by row in
`results/recovery_windows.csv` (`anchor_within_observable_log_interval`).

The comparison therefore runs on **74** recoveries. All 5 excluded ones are unmatched
under every window, so the observed numerator is 34 in both framings: 34/79 = 43.04%
on the headline denominator, 34/74 = 45.95% on the answerable one. The analysed set is
**identical across all three windows**, which is what makes the three columns of the
figure comparable; a test enforces this.

### 7.3 Resampling and repeated recoveries

21 experiments contain more than one labelled recovery, covering 52 of the 79, and one
of them holds four recoveries that share a single anchor. Two resampling schemes are
run so this cannot drive the result:

- `independent` — every recovery is redrawn separately. Simple, and the primary.
- `experiment_shift` — one offset per experiment rotates *all* of that experiment's
  anchors together, preserving their relative spacing, including the four identical
  ones. This is the check against the correlation that independent draws destroy.

Crossed with the two anchor domains, that is four variants per window, all in
`results/background_null.csv` with `variant_role` marking the primary.

**What `experiment_shift` does not cover.** Rotating by a uniform offset leaves the
*marginal* anchor distribution uniform: the mean relative position of a resampled
anchor in its domain is 0.4994 under both schemes, with matching deciles. So
`experiment_shift` controls the correlation between several anchors of one experiment,
and nothing else. It does **not** control temporal non-stationarity in event density —
if labelled anchors systematically fell in the denser phases of a run, a uniform null
would understate the background and inflate the ratio.

Measured, that bias runs the other way here. Real anchors sit in *sparser* than average
stretches of their own log: the ratio of local event density (±30 min around the anchor)
to the whole-log density has median 0.37 and mean 0.61, and only 15 of the 74 anchors
exceed their log's own average density. A uniform anchor therefore lands in busier
places than the real ones do, so the reported background is if anything too high and the
reported ratio too low. No phase-preserving null variant is run, because correcting a
conservative bias would only strengthen the result.

### 7.4 Statistics reported

`expected_fraction` (null mean), the null distribution as percentiles plus the full
matched-count histogram, `ratio_observed_over_expected`, and an empirical p-value
`(#{null >= observed} + 1) / (iterations + 1)`. The `+1` keeps the estimate from
reporting an impossible zero.

`analytic_expected_fraction` is an independent cross-check, not a second model. An
anchor `a` matches event `t` exactly when `t - post <= a <= t + pre`, so the exact
probability is the size of the union of those intervals clipped to the anchor domain,
divided by the domain size. It agrees with the simulated mean to under 0.01 in every
variant, which is asserted by a test.

### 7.5 What the null does and does not license

A ratio above one means operation-log rows cluster near recovery labels more than near
arbitrary instants of the same log. That is **temporal association only**. It is not
causal evidence, it does not identify which rows are recovery actions, and it does not
show that the labelled physical or operator recovery was itself observed.

One alternative explanation deserves stating plainly, because the null cannot rule it
out on its own. The anchor is the **end of the perturbation**, and in this plant a
perturbation is typically introduced and ended through the same operator interface that
writes the operation log. Rows such as *back to automatic mode* and *changed value* are
over-represented inside the matched windows relative to their share of all rows. So part
of the concentration could record the operator action that *ends the perturbation* —
which is the anchor itself — rather than anything that follows it.

The null was not tuned toward a conclusion. It contradicts one reading of the
sensitivity table: widening the window from `[-60 s, +120 s]` to `±600 s` raises
coverage from 34 to 37, but the background rises faster, and at `±600 s` the observed
value is statistically indistinguishable from random anchoring. The extra matches at
wide windows are what chance alone would produce.

### 7.6 Falsification check on that alternative

The check removes the coupled row classes and recomputes. The exclusion criterion is
structural and was fixed before the effect was inspected:

> Exclude a row whose `Property` records **an act that sets or restores a device's
> commanded state** — the mechanism by which a perturbation is applied and terminated.
> Because the anchor is `PerturbationMode.hasEnd`, such rows are expected at the anchor
> by construction of the perturbation protocol, whether or not a recovery occurred.

The rule is applied to the raw `Property` text rather than to `event_category`, and
three nested exclusion sets are reported so the boundary cannot be picked for effect:
`event_category` is a convenience field that no published metric reads, and its mapping
had misfiled two source spellings (`Automatic mode active` and `emergency _stop`) that
this check has to catch.

| Set | Adds | Rows removed |
| --- | --- | ---: |
| E1 | mode transitions (`back to automatic mode`, `automatic mode active`, `manual mode active`) and setpoint changes (`changed value…`, `changed_value_…`) | 1 201 (17.4%) |
| E2 | device toggles (`heater disabled`/`was enabled`, `disabled heater`, `AV709=1`) | 1 354 (19.6%) |
| E3 | deliberately over-broad: also `start`/`stop process clicked`, `process changed`, `Critical`, `Warning`, `Emergency Stop` | 1 803 (26.1%) |

Only the definition of a match changes; the anchor domain stays the one computed from
the full event set, so the same 74 recoveries, seed, and 10,000 iterations apply.

| Exclusion | Matched | Observed | Null | Ratio | Empirical *p* | Matches lost |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| none (primary) | 34/74 | 45.95% | 17.67% | 2.60× | 0.0001 | — |
| E1 | 33/74 | 44.59% | 15.90% | 2.80× | 0.0001 | 1 |
| E2 | 33/74 | 44.59% | 15.63% | 2.85× | 0.0001 | 1 |
| E3 | 32/74 | 43.24% | 15.32% | 2.82× | 0.0001 | 2 |

The enrichment survives, and slightly strengthens, because removing those classes lowers
the background faster than it lowers the observed coverage. At `±300 s` the ratio moves
from 1.33× to 1.39–1.42×; at `±600 s` it stays at 1.00× and remains indistinguishable
from background under every exclusion set.

**This falsifies one member of the objection, not the objection.** The residual signal is
carried mainly by recipe-engine rows — `P301 new step` appears in the windows at 10.7% of
its own base rate and the `H701`–`H708 new step` rows at 7.1–7.3%, against the ~1.8% of
logged time the 74 windows cover — plus HMI navigation rows such as *Opened window with
recipe steps*. Those may be coupled to the perturbation boundary in the same way, if the
controller resumes its step schedule when an override ends. Excluding them too leaves
1 110 of 6 903 rows and 4 matches, which has no power to discriminate and is reported
here only so that nobody mistakes it for a result.

So the conclusion is unchanged: the association is real and not an artefact of
mode-and-setpoint logging, but it remains association, and it is still not evidence that
the labelled physical or operator recovery was itself observed. Row-level references in
`results/recovery_windows.csv` (`matched_event_rows`, `candidate_evidence_timestamp`)
let a reader repeat the check.

These falsification figures come from a one-off analysis against the pinned inputs. They
are **not** regenerated by `scripts/reproduce.py` and are not committed as an artefact;
the criterion above is stated in full so the check can be reproduced independently.


## 8. Determinism and provenance

- Inputs are verified by byte size, SHA-256, and MD5 against `data/manifest.json`
  before anything is parsed. A mismatch aborts the run.
- Headline values are committed invariants in `manifest.json` `expected_metrics`. The
  run fails if any of them changes.
- Records stay in source order. Outputs contain no run timestamp and no random
  identifier, and the null uses a fixed seed, so a rerun against identical inputs is
  byte-stable. `results/SHA256SUMS.txt` pins all five generated artefacts, the figure
  included.
- The figure is written with PNG metadata suppressed so that it too is byte-stable
  across reruns with the locked matplotlib version.
- `notebooks/audit.ipynb` reads the generated files and computes nothing itself. It is
  executed by `scripts/execute_notebook.py`, which disables nbclient's per-cell
  wall-clock timings and assigns positional cell ids, so re-execution against identical
  results is byte-stable rather than churning the whole file.

## 9. Known limitations

- The Flex-Cat result covers one deposited Chemspeed run, not laboratory automation in
  general. The Batch Distillation result covers one plant.
- Time-of-day matching does not infer midnight rollover.
- The 60 transfer rows were produced by deterministic parsing and comparison. No
  separate manual-adjudication file was recovered; see [`MISSING_INPUTS.md`](MISSING_INPUTS.md).
- The null holds event structure fixed and moves only the anchor. It therefore tests
  the position of the labels against that log, not whether the log is a complete or
  faithful record of the plant.
- The background comparison is a within-experiment argument. It says nothing about
  what a differently instrumented plant would show.
- Public CI runs on synthetic fixtures because the upstream archives are not
  redistributed here. A green CI run does **not** mean the real-data audit was
  re-executed.

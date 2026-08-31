# Open PXRD Database

This project is the public interface for a traceable database of digitized
powder X-ray diffraction (PXRD) data. It is paper-centric: a researcher starts
from a publication, sees every indexed PXRD figure, compares the published crop
with reconstructed traces, and downloads the curve data.

## Current scope

- Paper index with paper number, title, DOI, journal, and year
- Search across paper metadata
- Side-by-side source crop and digitized plot
- Per-figure curve inventory and CSV download
- Supabase-ready public read model
- Google sign-in through Supabase Auth
- Authenticated private PDF uploads and digitization planning jobs
- Local demo snapshot containing three real records from `pxrd_fetcher`

The previous AI extraction screens are no longer part of the public product.
Legacy backend code remains in place for now so research tooling is not removed
destructively while the database interface is being rebuilt.

## Architecture

```text
React/Vite website
        |
        | anonymous public reads / authenticated private workspace
        v
Supabase Postgres  -- paper, figure, and curve metadata
Supabase Storage   -- source crops, digitized plots, overlays, CSV/Parquet files
```

The private workspace is deliberately a Phase-A shell: it stores PDFs, the
client-side cost estimate, and a hard budget cap, then leaves the job in
`saved`. A saved job is not a queue entry: a future worker must issue a new
server-side quote and receive explicit user approval before it can become
`queued`. Phase A does not collect a model credential or run extraction.
The existing legacy FastAPI backend is not the PXRD worker and must not be
exposed as one.

Dense curve points belong in Storage files rather than millions of Postgres
rows. This keeps browsing queries small while preserving full-resolution data
for download.

## Run locally

```bash
cd frontend
npm install
npm run dev
```

The demo works without credentials. For live data, run `supabase/schema.sql`
and add `VITE_SUPABASE_URL` plus `VITE_SUPABASE_ANON_KEY` to the project-root
`.env`. Vite only exposes variables with the `VITE_` prefix.

`supabase/schema.sql` is the definition for a fresh deployment. An already
deployed database is brought to the same shape by the files in
`supabase/migrations/`, because `create table if not exists` never adds a
column to an existing table. Each migration is idempotent and safe to re-run.

Do not expose a Supabase service-role key in the frontend. Curated writes and
bulk imports should run from a trusted local script or server environment.

## Private digitization workspace

After running `supabase/schema.sql`, run
`supabase/digitization_workspace.sql` in the Supabase SQL editor. This creates:

- `digitization_jobs` and `digitization_job_files`
- private `pxrd-user-uploads` and `pxrd-user-results` buckets
- owner-only RLS policies based on `auth.uid()`
- narrow RPCs for quota-controlled creation, manifest registration, saving,
  and deletion of unprocessed jobs

Configure Google in Supabase Auth and allow these application redirects:

```text
https://pxrd-peak.vercel.app/auth/callback
http://localhost:5173/auth/callback
```

The Google OAuth client secret belongs only in the Supabase provider settings.
No Google secret, service-role key, or model-provider key belongs in a `VITE_`
variable. The site currently accepts PDF inputs only because the production
PXRD pipeline does not yet have a hosted image-input entrypoint.

Phase A permits at most two active private jobs per user, and saved/draft jobs
can be deleted from the website. Keep the Google OAuth application in Testing
mode with an explicit tester list; public signup additionally requires rate
limiting and an automatic retention/expiration process.

## Import the deterministic Free-plan pilot

The importer selects complete papers from the clean set using a stable hash, so
rerunning the same limit always targets the same records. It converts source and
digitized images to lossless WebP, combines each figure's points into one
compressed CSV, skips existing objects, and upserts metadata.

```bash
# Inspect the first 10 papers without network writes
python3 scripts/import_pxrd_to_supabase.py --limit 10 --dry-run

# Smoke test
python3 -u scripts/import_pxrd_to_supabase.py --limit 10 --workers 4

# Free-plan pilot
python3 -u scripts/import_pxrd_to_supabase.py --limit 1000 --workers 8
```

The approved 1,000-paper pilot contains 1,866 figures and 3,815 clean curves.
The command is safe to resume after interruption because existing Storage
objects are detected and all metadata writes are idempotent upserts.

Run the migrations in date order before the first import that carries their
columns; without them the upsert fails on the unknown columns.

1. `supabase/migrations/20260828_verification_and_publication_status.sql`
2. `supabase/migrations/20260830_peaks_crystallinity_and_material_labels.sql`

Neither has been run against production. Migrations run in date order and older
ones are not re-run afterwards, so the 20260830 file deliberately leaves
`pxrd_paper_index` alone: `create or replace view` can neither drop nor reorder
columns, and redefining the view here would make the 20260828 file unrepeatable.

`--metadata-only` writes `pxrd_papers` and nothing else. It is the right command
for a metadata-only sync and the wrong one for publishing the pilot; only the
full path writes `pxrd_figures`, `pxrd_curves`, `pxrd_material_groups`, and the
Storage objects. The importer now says so at the end of every `--metadata-only`
run rather than leaving the omission silent.

Three flags are off by default:

- `--upload-overlays` also publishes `overlay.webp`, the extracted trace drawn
  on the source figure, and sets `overlay_path`. It roughly doubles the pilot's
  Storage footprint, which is why it is opt-in. While it is off the
  `overlay_path` key is omitted from the payload entirely, so any value already
  live is preserved rather than cleared.
- `--allow-unverified` publishes figures whose `result.json` could not be read.
  The importer refuses by default: those figures would be published as
  `axis_unverified`, and because the upsert merges duplicates that could
  downgrade a live `flagged` figure to `pending`. Attach the assets volume
  instead.
- `--allow-unchecked-publication-status` publishes papers whose local
  `publication_status` is empty as `unchecked`. Refused by default for the same
  reason: merge-duplicates would overwrite a live `retracted` with it. Run the
  Crossref backfill instead.

Every optional column is emitted only when the local SQLite column exists.
`resolution=merge-duplicates` builds its `UPDATE SET` list from the payload
keys, so an omitted key preserves the live value while a hardcoded `None`
would overwrite it. Every gate is table-level, never row-level: PostgREST builds
one column list per bulk insert and rejects a batch whose objects have differing
key sets (`PGRST102`), so a key added under a per-row condition would fail up to
300 rows at once.

## Running without the pipeline assets volume

`--assets-root` points at the pipeline run directory, which lives on an external
volume. Only the modes that read it require it:

| mode | volume detached |
| --- | --- |
| `--metadata-only` | runs; publishes `pxrd_papers`; reads no asset |
| `--dry-run` | runs; reports every figure as missing; exits 1 |
| full import | refuses before any network call, naming the missing path |

There is no flag combination that publishes a figure's `verification_status`
without the volume. `verification_status` comes from each figure's
`result.json`, so an unreadable tree makes every figure `axis_unverified`, and
`quality_status` degrades with it because the `axis_arbitrated` flag reason
cannot fire. The run summary says so in as many words, and the full import
refuses outright.

## Figure quality is machine verification, never human review

No human has reviewed any figure in this database. `quality_status` is
therefore derived, and the importer never emits `'reviewed'`; that value stays
reserved for a human-review workflow that does not exist. Earlier imports
hardcoded `'reviewed'` for all 1,866 pilot figures, which was false.

| `quality_status` | Meaning | Rule | Pilot |
| --- | --- | --- | --- |
| `flagged` | An automated check disputed this figure. Inspect before reuse. | `calibration.status == 'arbitrated'` or `figures.status == 'partial'` | 139 |
| `pending` | Automated extraction passed its own checks. Nothing has reviewed it. | everything else | 1,727 |
| `reviewed` | reserved for real human review | never emitted | 0 |

The pipeline fits the 2-theta axis twice, once from detected tick marks and
once from OCR of the axis labels, and compares the two fits across the plot
width against a 0.5 degree tolerance. `verification_status` reports which of
those happened:

| `verification_status` | Meaning | Pilot |
| --- | --- | --- |
| `axis_cross_validated` | the two fits agreed | 1,528 |
| `axis_single_method` | only one fit was available, so nothing cross-checked it | 261 |
| `axis_arbitrated` | the two fits disagreed and a second read broke the tie | 77 |
| `axis_unverified` | no `result.json` was readable | 0 |

`axis_single_method` deliberately does not flag. Nothing disputed the axis;
there was only one witness. That is a disclosure, not a warning.

The disagreement is worth surfacing because nothing else catches it. Of the 76
pilot figures with `axis_agreement_deg >= 0.5`, 45 have `confidence >= 0.99`
and their median axis RMSE is indistinguishable from the corpus median.
`confidence` measures how faithfully the polyline follows ink and `axis_rmse_deg`
measures how tightly the fit closes; neither can see a correctly traced curve
sitting on a wrong x-axis. An arbitrated axis is not known to be wrong: all 77
have physically plausible 2-theta ranges after the tie-break. The honest claim
is that the axis rests on arbitration rather than on agreement.

Numbers travel beside the badge so a reuser can judge for themselves:
`axis_agreement_deg`, `axis_rmse_deg`, `axis_tick_count`, `series_detected`,
`series_digitized`, `series_omitted_computed` per figure, and
`two_theta_uncertainty_deg`, `trace_confidence`, `snap_rate`,
`mean_snap_residual_px` per curve. Typical pilot values: 2-theta uncertainty
p5/p50/p95 = 0.021 / 0.046 / 0.102 degrees; axis RMSE p50/p95 = 0.012 / 0.038
degrees.

`series_omitted_computed` counts curves on a published figure that are not in
the clean set. All 86 in the pilot are unflagged, sit on unquarantined figures,
and carry trace confidence 0.89 to 0.999. They are excluded because the
pipeline classified them as computed rather than measured: labels such as
`Simulated`, `Pawley refined`, `Difference`, and stacking models like `AA
eclipsed` and `AB staggered`. The clean set is experimental data, so the
omission is disclosed rather than flagged.

`quarantined`, `quarantine_reasons`, `flagged`, and `flag_reasons` are not used
here. The clean set is defined as unflagged, unquarantined, and not computed,
and the importer selects only `in_clean_set=1` curves, so across the pilot all
1,866 figures have `quarantined = 0` and all 3,815 published curves have
`flagged = 0`. All four columns are constant over anything that reaches
Supabase; a badge built on them would be a second false claim in a different
colour.

Because computed curves are excluded by construction, the `simulated`,
`refined`, and `difference` values of `curve_role` can never appear on a
published curve, even though the column permits them.

## First peak and d-spacing

`first_peak_*` is the one peak the pipeline actually vetted. `tools/first_peak.py`
gates a candidate on signal-to-noise, persistence, width, and distance from the
plotted window edge, and records which gate it fell through.

| `first_peak_status` | Meaning | Position? | Pilot |
| --- | --- | --- | --- |
| `ok` | passed every gate | yes | 2,687 |
| `low_confidence` | a real peak, but weak or window-limited | yes | 1,029 |
| `truncated_at_window_start` | the figure begins part-way up a peak | no | 58 |
| `no_bragg_peak` | nothing Bragg-like in the plotted range | no | 41 |

Only 2,687 of 3,815 curves are `ok`. A search that silently returns all 3,716
curves carrying a position is a different search from the one the labels
promise; gate on `first_peak_status` and say which gate you used.

**d-spacing is derived, not measured.** `first_peak_d_angstrom` is
`lambda / (2 sin(theta))` with `lambda` taken from
`first_peak_wavelength_angstrom`, and `first_peak_wavelength_source` says where
that came from. Across the whole 2,370-paper corpus exactly one paper reports a
machine-readable wavelength, and none of its curves are in the clean set, so
every published curve is `assumed_cu_ka` — Cu Kα₁ 1.5406 Å, a default this
project chose. Filtering by d is filtering by 2θ under a unit relabel, and a
pattern collected on Mo Kα (0.7107 Å) is wrong by 2.17× in d. The wavelength
travels as data so a reuser can filter on the assumption rather than read about
it in a caption.

1,106 of 3,815 pilot curves have their first peak within 1.0° of the plotted
window start. It is the lowest-angle peak *in the published figure*, not
necessarily the material's lowest-angle reflection.

`first_peak_snr` is deliberately not published. `tools/first_peak.py` floors
sigma at `1e-3 * range`, so SNR saturates at a ceiling of 1000: the pilot median
is 966 and 16.1% of curves sit within 0.5 of the ceiling. On digitized vector
traces the MAD residual is often zero, so the number measures how smooth our own
vectorization was, not the signal quality of the original diffractogram. An SNR
filter built on it would rank our tracing, not the data.

`first_peak_fwhm_deg` is a displayable number, not a crystallite size. Its range
0.081–2.963° is the detector's own `[0.05, 3.0]` gate, so both extremes are
clipped by construction. No Scherrer analysis is defensible on it.

### The unvetted peak list

`peaks` is a JSONB array of `{two_theta, rel_height, prominence}`, ascending in
2θ — 33,126 entries over the pilot. It is a **different and much weaker
detector**: `tools/build_db.py` runs `find_peaks(prominence = 0.03 * range)` with
no smoothing and none of the four physics gates above, then keeps the top 40 by
prominence. Measured against the pilot:

- 60.5% of entries have prominence < 0.10
- the vetted first peak is absent from the list on 8.0% of curves
- 630 curves carry entries *below* their vetted first peak
- only 77.7% of curves have their lowest entry equal to the vetted first peak
- 136 curves hit the 40-peak cap, so their lists are incomplete —
  `peak_list_truncated` marks them

Raising the prominence gate makes the agreement worse, not better, so no
threshold reconciles the two detectors. The list is published because multi-peak
search is the query researchers actually want, and it is worth having as long as
every surface built on it says what it is. `peak_count` is the length of this
list, capped at 40 — it ranks trace noisiness, not crystallinity.

Search it with the `pxrd_search_peaks(targets, tol_deg, require_status)` RPC
rather than downloading it. The query is highly selective — one peak at ±0.1°
returns 230 of 3,815 pilot curves, two return 26, three return 1 — so pulling
241 KB gzip to the client to filter down to 26 rows is backwards by roughly
100×. The RPC widens the tolerance per curve to that curve's own
`two_theta_uncertainty_deg` (pilot median 0.046°, p95 0.102°) so it is never
finer than the axis calibration supports, and clamps `tol_deg` into
`[0.02, 1.0]`. A tolerance below ±0.1° is below the axis uncertainty of about
half the corpus.

## Crystallinity descriptors

Availability is a four-state enum because absence of data and a low value are
different facts:

| `stacking_hump_status` | Meaning | Pilot |
| --- | --- | --- |
| `hump_detected` | a stacking hump was fitted in 15–35° | 3,385 |
| `no_hump_detected` | the region was plotted and no hump was found — a real negative | 248 |
| `window_not_covered` | under 3° of 15–35° was plotted, so the figure cannot say | 182 |
| `not_computed` | no descriptor row exists for this curve | 0 |

The 182 are figures that never plotted the hump region — 170 stop below 18°, 12
start above 32°. A filter keyed on `stacking_hump_center_deg IS NULL` merges
them with the 248 genuine negatives and reports a no-data curve as well-ordered.
`stacking_hump_center_deg`, `stacking_hump_fwhm_deg`, and
`intensity_ratio_100_001` exist only under `hump_detected`, and the database
enforces that rather than trusting the importer.

`crystalline_fraction` is published as a per-curve diagnostic, **not as a filter
axis.** `tools/descriptors.py` defines it as

```text
sum(y - rolling 10th-percentile baseline over 4 deg) / sum(y - min(y))
```

which is the fraction of integrated intensity carried by features narrower than
about 4°. It is not degree of crystallinity, not a Rietveld or Ruland quantity,
and has no relation to crystalline weight fraction. It is also confounded by how
much of the pattern the authors chose to plot, because a wider window adds
high-angle background to the denominator:

| plotted 2θ span | median `crystalline_fraction` |
| --- | --- |
| under 20° | 0.852 |
| 27.6–33.1° | 0.757 |
| 33.1–44.3° | 0.712 |
| 80–100° | 0.564 |

A 0.29 swing driven by figure layout, not by the material. Two curves of the
same sample plotted over different ranges get materially different values, so a
`crystalline_fraction >= 0.7` threshold across the corpus is not defensible.
Read it beside `two_theta_min` and `two_theta_max` on the same row.

`intensity_ratio_100_001` is the one scale-free descriptor here: a within-curve
height ratio, available on 3,304 of 3,815 curves. `stacking_hump_height` is not
published — it is in un-normalized relative-intensity units (0.061–102.7) and is
not comparable between curves.

## Cross-paper material labels

`pxrd_material_groups` links curves whose published `material_name` normalises
to the same string. **This is a claim about label agreement, not about material
identity.** Nobody has verified that two curves in a group are the same
material, labels are reused and redefined between groups, and every surface
should say "labelled X" rather than "the same material".

Normalisation folds formatting and keeps composition: markup, unicode
sub/superscripts, Greek letters, and every dash variant are normalised, so
`Fe₃O₄` and `Fe3O4` group, as do `Tp-Azo`/`TpAzo` and `TpPa-SO3H`/`TpPa-SO₃H`.
`@` and `/` are kept as identity-bearing separators: deleting them would merge
`COF@S` with the plural `COFs`, and `COF/MXene` with `COF@MXene`.

The gate matters more than the matching. The largest label groups are the least
meaningful, so `specificity` refuses three classes and only `specific` groups
ever link:

| `specificity` | Example | Multi-paper groups |
| --- | --- | --- |
| `specific` | `TpPa-1`, `COF-300`, `Fe₃O₄` | 42 |
| `paper_local` | `M-COF`, `H-COF1`, `GO` | 26 |
| `serial` | `COF-1`, `COF-A`, `compound 2` | 19 |
| `generic` | `COF`, `COFs`, `Imine-COF`, `Pure COF` | 12 |

The biggest label in the pilot is the literal word "COF" across 26 papers, and
one paper alone supplies COF-1 through COF-5. Publishing those would assert that
one paper's COF-2 *is* another paper's COF-2. `COF-A` and `COF-Br` are the same
failure mode in letters. The rule is that a bare class stem plus a short local
tag — one or two digits, or a token of up to three letters — does not link;
three or more digits (`COF-300`) and two-to-three letters followed by a number
(`COF-LZU1`) survive, because those are the literature's catalogue form.

This costs real recall. `ZIF-90`, `CTF-1`, and `NKCOF-1` are genuine catalogue
names that the gate refuses, because no string test separates them from a
paper-local tag. A missing link is a disclosure; a fabricated one is a false
claim, so the refusal is the safe direction.

Over the pilot: **42 labels appear in more than one paper**, covering 110 papers
and 174 of 3,815 curves. `material_group_id` is set only on those curves; NULL
means "not linked", never "unique material". The 57 refused multi-paper groups
are published too, with their `specificity`, so the gate is inspectable — but no
curve is allowed to point at one. `match_basis` is `label_identical` when every
folded label was byte-identical (57 groups) and `label_normalised` otherwise
(42); there is deliberately no score, because no calibrated probability exists
behind this and inventing one would be the overclaim the gate exists to prevent.

Provenance is `pxrd_curves.material_name` alone. The local `peter_match` table is
not used and no third-party dataset is read: `peter_match.peter_row` is DOI-gated
and so cannot express cross-paper identity at all — all 475 tier-A pilot groups
span exactly one paper — and `matched_name`, its only cross-paper field, is a
copy of a third-party content column this project may not redistribute. It also
loses on the merits: it finds 18 multi-paper groups, 17 of which
`material_name` already finds.

## Zero-API title enrichment

Most source PDFs contain reliable embedded title metadata even when the local
paper table does not. The backfill script reads that metadata locally; it makes
no network or model calls and records every proposed change before applying it.

```bash
# Audit only
python3 scripts/backfill_pdf_titles.py --limit 1000

# Update the local SQLite title field
python3 scripts/backfill_pdf_titles.py --limit 1000 --apply

# Synchronize only paper metadata to Supabase (no asset upload)
python3 scripts/import_pxrd_to_supabase.py --limit 1000 --metadata-only
```

The metadata-only importer reads only `SUPABASE_URL` and
`SUPABASE_SECRET_KEY`; it never loads or invokes a model-provider credential.

## Crossref metadata backfill

`journal`, `publication_year`, and `authors` are empty for every imported paper,
so the site's journal and year filters, the paper `Meta` panel, and the CSV
export all render blank. Every paper has a DOI, so the public Crossref REST API
can supply those three fields and can also resolve the titles that embedded PDF
metadata missed.

The script is audit-only unless `--apply` is given. It makes no model calls and
no Supabase calls; publishing is the separate `--metadata-only` import step.

```bash
# Audit only: writes a JSON report, touches no rows
python3 scripts/backfill_crossref_metadata.py --limit 1000 --refresh

# Write journal, year, authors, and unresolved titles to local SQLite
python3 scripts/backfill_crossref_metadata.py --limit 1000 --refresh --apply

# Publish the result (paper metadata only, no asset upload)
python3 scripts/import_pxrd_to_supabase.py --limit 1000 --metadata-only
```

Run it after `backfill_pdf_titles.py`, which resolves titles offline and leaves
less for Crossref to fill.

`--refresh` is not optional here any more. All 1,000 pilot papers already carry
a `crossref_fetched_at` from the first sweep, and the script skips a paper that
has one, so without `--refresh` it adds the four publication-status columns as
NULL and writes no status at all. The importer then refuses the publish rather
than filling those NULLs with `active`. For retraction detection over the whole
corpus, use the `--all-papers` form in the next section.

Batches of 50 DOIs cover the pilot in 20 requests. `--mailto ADDRESS` (or
`CROSSREF_MAILTO`) opts into Crossref's polite pool for better rate limits; it
is unset by default and no address is stored in the repository.

Resolved titles are left alone, so the audit is the record of what Crossref
would have said instead. Two exceptions are automatic: an unresolved title is
filled, and a local title carrying mis-decoded bytes that Crossref renders
cleanly is repaired. `--prefer-crossref-titles` replaces every diverging title.

The publication year is the first usable of `published-print`,
`published-online`, `issued`. `issued` alone is Crossref's earliest deposited
date and runs a year early for the roughly 7% of this corpus that appeared
online in December and in print the following January.

Reruns are cheap: a paper carrying a Crossref timestamp is skipped unless
`--refresh` is passed. A network or HTTP failure is never recorded as a Crossref
verdict, so the affected papers stay untouched, the run exits non-zero, and the
same command retries exactly those papers.

## Retractions and corrections

The same backfill records `publication_status` per paper: `active`,
`retracted`, `withdrawn`, `concern`, or `corrected`. It is detected from two
Crossref signals, and both are needed because in this corpus they are
disjoint.

1. `updated-by` is the typed Crossmark link on the retracted or corrected work,
   naming the notice. It is authoritative and machine-readable, and it lags:
   a publisher that does not deposit its own Crossmark updates only acquires
   the link when the Retraction Watch ingest catches up, months later.
2. The article-side `RETRACTED:` / `RETRACTED ARTICLE:` / `WITHDRAWN:` title
   stamp is immediate but heuristic, and is the only timely signal for those
   publishers.

Over the 2,370-DOI corpus `updated-by` found 11 corrections and zero
retractions; the title stamp found 2 retractions and zero corrections.
Implementing either alone misses one category entirely. `relation` is empty on
every record and `type` is `journal-article` for retracted papers, corrections,
and retraction notices alike, so neither is usable.

Direction matters. `update-to` and `updated-by` are strict inverses: a record
carrying `update-to` is the notice and points back at the work it retracts, so
it is never flagged. Notice-shaped titles (`Retraction of "X"`, `Correction
to X`, `Corrigendum`, `Erratum`, `Expression of Concern`) are an explicit
negative for the same reason. `Expression of Concern:` is deliberately not an
article-side stamp, because Springer uses that form for the notice.

The stamp is stripped from any title adopted from Crossref, and the badge
carries the meaning instead. The publisher agrees the stamp is not the title:
its own retraction notice quotes the work without it. Keeping it would also be
inconsistent, since a title resolved from the PDF never carries one; it would
break title search and alphabetical sort; and it would reach a screen reader as
ordinary title text. Every strip is listed under `stripped_title_stamps` in the
report, and the rule is restated in `title_stamp_rule`.

A `publication_status_source` of `manual` is never overwritten by a Crossref
sweep. Anything preserved that way is reported under
`publication_status_manual_preserved`.

Retracted papers are not hidden. A scientific database should show the record
and mark it; dropping it would break DOI-stable links and conceal the very
thing the flag exists to disclose.

`unchecked` is a fifth value and is not a synonym for `active`. It means no
Crossref lookup has been recorded for that paper, and
`publication_status_checked_at` is the evidence that separates the two. The
20260828 migration declared the column `not null default 'active'`, which made
"never checked" unrepresentable and stamped a clean bill of health on all 1,000
live papers before Crossref was consulted; the 20260830 migration adds the value,
adds the evidence column, and demotes exactly the rows that were only defaulted.
The frontend must render `unchecked` as "publication status not checked", not as
a clean record.

Ship the steps in order, in one deploy. Stripping the title stamp without the
badge would remove a signal without replacing it.

```bash
# 1. Migrations, in date order, in the Supabase SQL editor.
#    20260828_verification_and_publication_status.sql
#    20260830_peaks_crystallinity_and_material_labels.sql

# 2. Full corpus, audit only
python3 scripts/backfill_crossref_metadata.py --all-papers --limit 2370 --refresh

# 3. Write it locally
python3 scripts/backfill_crossref_metadata.py --all-papers --limit 2370 --refresh --apply

# 4. Publish. This is the step that writes pxrd_figures, pxrd_curves,
#    pxrd_material_groups and the Storage objects. --metadata-only does NOT:
#    it writes pxrd_papers and returns.
#    Requires the assets volume mounted at ../outputs/full-run-01.
python3 -u scripts/import_pxrd_to_supabase.py --limit 1000 --workers 8
```

Step 4 replaces an earlier recipe that ended in `--metadata-only`. That command
issues exactly one `POST /rest/v1/pxrd_papers` and no Storage request at all, so
following it published paper metadata and left every figure, curve, quality
badge, and descriptor at whatever was already live. The importer now prints what
`--metadata-only` did not write.

The assets volume is a precondition for step 4 and therefore for the whole
deploy. Do not run step 1 without it: the migration collapses the false
`reviewed` badge and adds `verification_status`, and until step 4 runs, all 1,866
figures sit on the `axis_unverified` default — a badge the pilot data says
applies to none of them.

The importer refuses to publish a `publication_status`,
`publication_status_source`, or `publication_status_updated` it cannot
validate, rather than coercing it. Coercing an unrecognised status to `active`
would silently unflag a retracted paper, because the upsert merges duplicates.
An absent status is refused for the same reason: it would publish as `unchecked`
and overwrite a live `retracted`. A verdict whose `crossref_fetched_at` is
missing is refused too — the backfill writes both in one statement, so a verdict
without its evidence is a corrupted local row, not a finding.

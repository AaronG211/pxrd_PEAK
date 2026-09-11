-- ⚠️  SUPABASE SQL EDITOR: strip the `begin;` / `commit;` in this file before
--     pasting. The editor wraps your script in its own transaction; a nested one
--     can fail partway, roll back everything, and still report "Success. No rows
--     returned". Every statement here is individually guarded, so running them
--     without an enclosing transaction is safe. See ../README.md, "Running
--     migrations in the SQL editor". The wrapper is correct for psql; keep it.
--
-- Open PXRD Database migration: how a curve earned its place in the corpus.
--
-- Run once in the Supabase SQL editor, AFTER
-- 20260830_peaks_crystallinity_and_material_labels.sql and BEFORE re-running
-- scripts/import_pxrd_to_supabase.py. Idempotent; destroys no data.
--
-- ---------------------------------------------------------------------------
-- WHY
-- ---------------------------------------------------------------------------
-- The corpus was defined by curves.in_clean_set, which requires that no OTHER
-- curve in the same figure was flagged. That figure-level rule is a proxy: the
-- offline filter reads curve SHAPE and is blind to a misread 2-theta axis, and
-- since every curve in a panel shares one axis, a flagged neighbour is
-- circumstantial evidence the panel itself was misread.
--
-- The pipeline also measures that directly. It fits the axis twice per figure -
-- once from tick marks, once from OCR of the axis labels - and records whether
-- the two agreed. Where they did, the proxy's suspicion has a direct answer.
-- 1,672 curves were individually unflagged, experimental, and sat in a figure
-- whose axis was cross-validated; 238 papers reach this database only through
-- them. See tools/admit_axis_verified.py for the derivation and the report.
--
-- Worth knowing before reading `axis_verified` as second-class: 100% of those
-- curves sit in a cross-validated figure, against 82.6% of the existing clean
-- ones. They clear a calibration bar 644 of the 3,694 clean figures never had
-- to clear. What is true of them, and is not true of a clean curve, is that a
-- sibling trace in the same panel failed a shape check - which is why they are
-- labelled per curve rather than silently merged.
--
-- in_clean_set is NOT redefined, and it is worth being exact about why, because
-- an earlier draft of this comment overstated it.
--
-- in_clean_set is NOT the corpus the paper is written against. The paper
-- releases 2,370 papers / 4,664 figures / 11,445 calibrated curves - every
-- curve the pipeline accepted, with the 1,362 flagged ones disclosed rather
-- than removed. 7,713 appears in the paper exactly once, in the validation
-- section, as the endpoint of the quality funnel: "Of the 11,445, 1,362 curves
-- are flagged and 967 of 4,664 figures quarantined (3,582 curves), leaving
-- 7,713 clean."
--
-- That single sentence is still reason enough to freeze the column, and there
-- is a second: ten-odd result files under outputs/analysis/ are computed over
-- the clean set. Redefining in_clean_set would stop that sentence and those
-- files reproducing from outputs/pxrd.db.
--
-- Worth knowing while reading the rest of this file: the site publishes 9,385
-- curves, which is neither 7,713 nor 11,445. The 2,060-curve difference from
-- the paper's released resource is a choice this database has not yet made
-- deliberately.

begin;

-- ---------------------------------------------------------------------------
-- 1. How each published curve was admitted
-- ---------------------------------------------------------------------------
-- Default 'clean' is what every existing row is: the table held only clean-set
-- curves before this migration, so the default states the truth about them
-- rather than requiring a backfill pass to establish it.
alter table public.pxrd_curves
  add column if not exists admission text not null default 'clean';

-- Postgres has no "add constraint if not exists" for table constraints, so
-- drop-then-add is the idempotent form.
alter table public.pxrd_curves
  drop constraint if exists pxrd_curves_admission_check;
alter table public.pxrd_curves
  add constraint pxrd_curves_admission_check
  check (admission in ('clean', 'axis_verified'));

-- 'excluded' is deliberately NOT a legal value. Unpublished curves are not
-- rows in this table; a state meaning "present but not part of the corpus"
-- would be unreachable, and unreachable states get reintroduced by mistake.

-- in_clean_set must keep agreeing with admission, or the two readings of the
-- corpus drift apart silently and the 7,713 figure stops being checkable.
alter table public.pxrd_curves
  drop constraint if exists pxrd_curves_admission_clean_set_check;
alter table public.pxrd_curves
  add constraint pxrd_curves_admission_clean_set_check
  check (in_clean_set = (admission = 'clean'));

-- The browse filter's predicate.
create index if not exists pxrd_curves_admission_idx
  on public.pxrd_curves (admission);

-- ---------------------------------------------------------------------------
-- 2. Splitting the figure-level disclosure that was one number
-- ---------------------------------------------------------------------------
-- series_omitted_computed counted count(*) where in_clean_set = 0, with no
-- filter, and the UI rendered it as "N curves not digitized". Both halves were
-- wrong: the curves were digitized - they exist, with points, in the source
-- database - and most were not computed traces but ordinary curves that failed
-- a shape check or sat beside one that did.
--
-- With the cause now recorded per curve, the two are counted apart, and
-- series_omitted_computed finally means what its name has always claimed:
-- simulated / Pawley-refined / difference traces, excluded by policy.
alter table public.pxrd_figures
  add column if not exists series_excluded_quality integer not null default 0;

alter table public.pxrd_figures
  drop constraint if exists pxrd_figures_verification_range_check;
alter table public.pxrd_figures
  add constraint pxrd_figures_verification_range_check check (
    (axis_agreement_deg is null or axis_agreement_deg >= 0)
    and (axis_rmse_deg is null or axis_rmse_deg >= 0)
    and (axis_tick_count is null or axis_tick_count >= 0)
    and (series_detected is null or series_detected >= 0)
    and (series_digitized is null or series_digitized >= 0)
    and series_omitted_computed >= 0
    and series_excluded_quality >= 0
  );

-- ---------------------------------------------------------------------------
-- 3. pxrd_paper_index: which papers survive the clean-set-only filter
-- ---------------------------------------------------------------------------
-- `create or replace view` may only APPEND columns, never drop or reorder
-- them, so this repeats the 20260828 definition verbatim and adds one column
-- at the end. Every column added to this view is permanent; think before
-- adding another.
--
-- curve_count stays the count of ALL published curves - what the page shows.
-- clean_curve_count is what the default browse filter needs: a paper whose
-- clean_curve_count is 0 exists in the database and has a working page, but is
-- reachable only with the re-admitted curves included.
create or replace view public.pxrd_paper_index
with (security_invoker = true)
as
select
  p.id,
  p.paper_number,
  p.doi,
  p.title,
  p.authors,
  p.journal,
  p.publication_year,
  count(distinct f.id)::integer as figure_count,
  count(distinct c.id)::integer as curve_count,
  count(distinct c.material_name)::integer as material_count,
  p.publication_status,
  p.publication_status_notice_doi,
  count(distinct f.id) filter (
    where f.verification_status = 'axis_arbitrated'
  )::integer as figures_axis_disputed,
  count(distinct f.id) filter (
    where f.quality_status = 'flagged'
  )::integer as figures_flagged,
  count(distinct c.id) filter (
    where c.admission = 'clean'
  )::integer as clean_curve_count,
  -- Figures that still have a curve under the clean-set-only reading. A figure
  -- whose every trace was re-admitted disappears from that view along with its
  -- curves, so the masthead's three numbers stay one consistent reading of the
  -- corpus rather than mixing two.
  count(distinct f.id) filter (
    where c.admission = 'clean'
  )::integer as clean_figure_count
from public.pxrd_papers p
left join public.pxrd_figures f on f.paper_id = p.id
left join public.pxrd_curves c on c.figure_id = f.id
group by p.id;

grant select on public.pxrd_paper_index to anon, authenticated;

commit;

-- If section 3 fails with "cannot change name of view column", an older
-- definition of the view is installed. Run `drop view public.pxrd_paper_index;`
-- and re-run this file.
--
-- VERIFY (run separately; the SQL editor reports "Success" for a rolled-back
-- script, so check the numbers rather than the status line):
--   select admission, count(*) from public.pxrd_curves group by 1;
--     -> clean 7713, axis_verified 1672   (after the importer re-runs)
--   select count(*) from public.pxrd_paper_index where clean_curve_count = 0;
--     -> 238
--   select sum(clean_curve_count), sum(curve_count) from public.pxrd_paper_index;
--     -> 7713, 9385

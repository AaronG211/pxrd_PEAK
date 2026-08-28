-- Open PXRD Database migration: machine-verification signals and paper
-- publication status (retractions, withdrawals, corrections).
--
-- Run this once in the Supabase SQL editor BEFORE re-running
-- scripts/import_pxrd_to_supabase.py. It is idempotent: every statement is
-- guarded, so re-running it is a no-op. It destroys no data.
--
-- The whole script runs in one transaction so the pxrd_paper_index view is
-- never observably absent to a concurrent reader.
--
-- Both halves live in one file on purpose. `create or replace view` may only
-- APPEND columns, so the view has exactly one final definition (section 3);
-- splitting the two halves into two files would make them order-dependent and
-- no longer re-runnable.

begin;

-- ---------------------------------------------------------------------------
-- 1. Figure-level machine verification
-- ---------------------------------------------------------------------------
-- quality_status keeps its existing CHECK ('reviewed', 'pending', 'flagged').
-- 'reviewed' is now RESERVED for a human-review workflow that does not exist
-- yet, and the importer never emits it. See section 1c.

alter table public.pxrd_figures
  add column if not exists verification_status text not null default 'axis_unverified';

alter table public.pxrd_figures
  add column if not exists axis_agreement_deg double precision;
alter table public.pxrd_figures
  add column if not exists axis_rmse_deg double precision;
alter table public.pxrd_figures
  add column if not exists axis_tick_count integer;
alter table public.pxrd_figures
  add column if not exists series_detected integer;
alter table public.pxrd_figures
  add column if not exists series_digitized integer;
alter table public.pxrd_figures
  add column if not exists series_omitted_computed integer not null default 0;

-- 1a. Constraints. Postgres has no "add constraint if not exists" for table
-- constraints, so drop-then-add is the idempotent form.
alter table public.pxrd_figures
  drop constraint if exists pxrd_figures_verification_status_check;
alter table public.pxrd_figures
  add constraint pxrd_figures_verification_status_check
  check (verification_status in (
    'axis_cross_validated',
    'axis_single_method',
    'axis_arbitrated',
    'axis_unverified'
  ));

alter table public.pxrd_figures
  drop constraint if exists pxrd_figures_verification_range_check;
alter table public.pxrd_figures
  add constraint pxrd_figures_verification_range_check
  check (
    (axis_agreement_deg is null or axis_agreement_deg >= 0)
    and (axis_rmse_deg is null or axis_rmse_deg >= 0)
    and (axis_tick_count is null or axis_tick_count >= 0)
    and (series_detected is null or series_detected >= 0)
    and (series_digitized is null or series_digitized >= 0)
    and series_omitted_computed >= 0
  );

-- 1b. Curve-level fidelity numbers. These already exist per curve in the local
-- SQLite database and were simply never published.
alter table public.pxrd_curves
  add column if not exists two_theta_uncertainty_deg double precision;
alter table public.pxrd_curves
  add column if not exists trace_confidence double precision;
alter table public.pxrd_curves
  add column if not exists snap_rate double precision;
alter table public.pxrd_curves
  add column if not exists mean_snap_residual_px double precision;

alter table public.pxrd_curves
  drop constraint if exists pxrd_curves_fidelity_range_check;
alter table public.pxrd_curves
  add constraint pxrd_curves_fidelity_range_check
  check (
    (two_theta_uncertainty_deg is null or two_theta_uncertainty_deg >= 0)
    and (trace_confidence is null or (trace_confidence >= 0 and trace_confidence <= 1))
    and (snap_rate is null or (snap_rate >= 0 and snap_rate <= 1))
    and (mean_snap_residual_px is null or mean_snap_residual_px >= 0)
  );

-- 1c. Retire the false 'reviewed' value. No human has reviewed any figure, so
-- collapse the claim now. This UPDATE and the importer change that stops
-- emitting 'reviewed' must ship together, or the next import restores it.
update public.pxrd_figures
set quality_status = 'pending', updated_at = now()
where quality_status = 'reviewed';

create index if not exists pxrd_figures_verification_status_idx
  on public.pxrd_figures (verification_status);

-- ---------------------------------------------------------------------------
-- 2. Paper-level publication status
-- ---------------------------------------------------------------------------
alter table public.pxrd_papers
  add column if not exists publication_status text not null default 'active';
alter table public.pxrd_papers
  add column if not exists publication_status_notice_doi text;
alter table public.pxrd_papers
  add column if not exists publication_status_source text;
alter table public.pxrd_papers
  add column if not exists publication_status_updated date;

alter table public.pxrd_papers
  drop constraint if exists pxrd_papers_publication_status_check;
alter table public.pxrd_papers
  add constraint pxrd_papers_publication_status_check
  check (publication_status in
    ('active', 'retracted', 'withdrawn', 'concern', 'corrected'));

alter table public.pxrd_papers
  drop constraint if exists pxrd_papers_publication_status_source_check;
alter table public.pxrd_papers
  add constraint pxrd_papers_publication_status_source_check
  check (publication_status_source is null or publication_status_source in
    ('crossref-update', 'crossref-title', 'manual'));

-- 13 of 2,370 corpus papers are non-active, so a partial index keeps any
-- "hide retracted" filter and any admin audit query cheap.
create index if not exists pxrd_papers_publication_status_idx
  on public.pxrd_papers (publication_status)
  where publication_status <> 'active';

-- ---------------------------------------------------------------------------
-- 3. The single final definition of the paper index view
-- ---------------------------------------------------------------------------
-- `create or replace view` may only append columns, never reorder or rename
-- them, so every new column goes after material_count and this ordering is
-- now fixed. `security_invoker` must be restated: replacing a view replaces
-- its reloptions.
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
  )::integer as figures_flagged
from public.pxrd_papers p
left join public.pxrd_figures f on f.paper_id = p.id
left join public.pxrd_curves c on c.figure_id = f.id
group by p.id;

grant select on public.pxrd_paper_index to anon, authenticated;

commit;

-- If section 3 fails with "cannot change name of view column", an earlier
-- draft of this view is installed. Run `drop view public.pxrd_paper_index;`
-- and then re-run this file.

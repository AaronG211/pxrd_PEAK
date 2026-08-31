-- Open PXRD Database migration: first-peak geometry, the unvetted peak list,
-- COF crystallinity descriptors, cross-paper material LABEL groups, and an
-- honest "nobody checked this yet" value for publication_status.
--
-- Run this once in the Supabase SQL editor, AFTER
-- 20260828_verification_and_publication_status.sql and BEFORE re-running
-- scripts/import_pxrd_to_supabase.py. Migrations run in date order and older
-- ones are never re-run afterwards.
--
-- It is idempotent: every statement is guarded, so re-running it is a no-op.
-- It destroys no data. The whole file runs in one transaction.
--
-- This file deliberately does NOT redefine public.pxrd_paper_index. Nothing
-- here needs a paper-level aggregate, and `create or replace view` can neither
-- drop nor reorder columns, so every column added to that view is permanent.
-- Leaving it alone keeps the 20260828 definition re-runnable.
--
-- Honesty notes encoded as schema, not as frontend copy:
--   * d-spacing is DERIVED from an ASSUMED wavelength. The wavelength value and
--     its provenance are stored per row, so no reader has to trust a UI string.
--   * first_peak_snr is deliberately NOT published. tools/first_peak.py floors
--     sigma at 1e-3 * range, so SNR saturates at 1000 (pilot median 966, 16% of
--     curves within 0.5 of the ceiling). It measures how smooth our own
--     vectorization was, not signal quality. Publishing it would invite exactly
--     the misreading this database exists to prevent.
--   * stacking_hump_height is NOT published: un-normalized relative-intensity
--     units (pilot range 0.061-102.7), not comparable between curves.
--     intensity_ratio_100_001 is the scale-free form of the same idea.
--   * stacking_hump_status is a FOUR-state enum. "no hump was found",
--     "the hump region was never plotted", and "no descriptor was computed at
--     all" are three different facts and must never collapse into one NULL.
--   * material groups are LABEL agreement, never verified material identity.
--     Every column name says label, and the specificity gate keeps generic and
--     serial labels from linking anything.

begin;

-- ---------------------------------------------------------------------------
-- 1. First-peak geometry on pxrd_curves
-- ---------------------------------------------------------------------------
alter table public.pxrd_curves
  add column if not exists first_peak_two_theta_deg double precision;
alter table public.pxrd_curves
  add column if not exists first_peak_d_angstrom double precision;
alter table public.pxrd_curves
  add column if not exists first_peak_fwhm_deg double precision;
alter table public.pxrd_curves
  add column if not exists first_peak_status text;
-- The wavelength travels as a VALUE plus a PROVENANCE, replacing the local
-- opaque 0/1 integer. In the 1,000-paper pilot every published curve is
-- 'assumed_cu_ka'; the column exists so that stops being invisible.
alter table public.pxrd_curves
  add column if not exists first_peak_wavelength_angstrom double precision;
alter table public.pxrd_curves
  add column if not exists first_peak_wavelength_source text;

-- 1a. Constraints. Postgres has no "add constraint if not exists" for table
-- constraints, so drop-then-add is the idempotent form.
alter table public.pxrd_curves
  drop constraint if exists pxrd_curves_first_peak_status_check;
alter table public.pxrd_curves
  add constraint pxrd_curves_first_peak_status_check
  check (first_peak_status is null or first_peak_status in (
    'ok',                        -- pilot 2,687: passed SNR/persistence/width/edge gates
    'low_confidence',            -- pilot 1,029: real peak, weak or window-limited
    'truncated_at_window_start', -- pilot    58: no position, plot began mid-peak
    'no_bragg_peak'              -- pilot    41: no position, nothing Bragg-like
  ));

alter table public.pxrd_curves
  drop constraint if exists pxrd_curves_first_peak_wavelength_source_check;
alter table public.pxrd_curves
  add constraint pxrd_curves_first_peak_wavelength_source_check
  check (first_peak_wavelength_source is null
         or first_peak_wavelength_source in ('assumed_cu_ka', 'paper_reported'));

-- Ranges are the detector's own gates (tools/first_peak.py: FWHM_MIN 0.05,
-- FWHM_MAX 3.0) plus physical bounds. Pilot extrema sit inside these:
-- 2-theta 0.541-73.31, d 1.29-163.25, FWHM 0.081-2.963.
alter table public.pxrd_curves
  drop constraint if exists pxrd_curves_first_peak_range_check;
alter table public.pxrd_curves
  add constraint pxrd_curves_first_peak_range_check
  check (
    (first_peak_two_theta_deg is null
      or (first_peak_two_theta_deg > 0 and first_peak_two_theta_deg < 180))
    and (first_peak_d_angstrom is null or first_peak_d_angstrom > 0)
    and (first_peak_fwhm_deg is null
      or (first_peak_fwhm_deg >= 0.05 and first_peak_fwhm_deg <= 3.0))
    and (first_peak_wavelength_angstrom is null
      or (first_peak_wavelength_angstrom > 0.4
          and first_peak_wavelength_angstrom < 3.0))
  );

-- A position with no wavelength is an unreadable d-spacing, and a status that
-- says "no position" carrying a position is incoherent. Measured on the pilot:
-- two_theta, d and FWHM are null together on exactly the 99 curves whose status
-- is truncated_at_window_start or no_bragg_peak.
alter table public.pxrd_curves
  drop constraint if exists pxrd_curves_first_peak_coherence_check;
alter table public.pxrd_curves
  add constraint pxrd_curves_first_peak_coherence_check
  check (
    (first_peak_two_theta_deg is null) = (first_peak_d_angstrom is null)
    and (first_peak_two_theta_deg is null) = (first_peak_fwhm_deg is null)
    and (first_peak_two_theta_deg is null)
        = (first_peak_wavelength_angstrom is null)
    and (first_peak_wavelength_angstrom is null)
        = (first_peak_wavelength_source is null)
    and (first_peak_status not in ('truncated_at_window_start', 'no_bragg_peak')
         or first_peak_two_theta_deg is null)
  );

-- 70.4% of published curves are 'ok'; the default search gates on it.
create index if not exists pxrd_curves_first_peak_search_idx
  on public.pxrd_curves (first_peak_status, first_peak_two_theta_deg);
create index if not exists pxrd_curves_first_peak_d_idx
  on public.pxrd_curves (first_peak_d_angstrom)
  where first_peak_d_angstrom is not null;

-- ---------------------------------------------------------------------------
-- 2. Crystallinity descriptors on pxrd_curves (descriptors is 1:1 with curves)
-- ---------------------------------------------------------------------------
-- crystalline_fraction is published for TRANSPARENCY, not as a filter axis.
-- tools/descriptors.py defines it as
--     sum(y - rolling 10th-pct baseline over 4 deg) / sum(y - min(y))
-- i.e. the fraction of integrated intensity carried by features narrower than
-- about 4 degrees. It is NOT degree of crystallinity, has no relation to
-- crystalline weight fraction, and is strongly confounded by how much of the
-- pattern the authors chose to plot: pilot medians run 0.852 for spans under
-- 20 deg down to 0.564 for 80-100 deg, a 0.29 swing that is a figure-layout
-- artifact. Read it beside two_theta_min / two_theta_max on the same row, and
-- do not threshold it across curves with different windows.
alter table public.pxrd_curves
  add column if not exists crystalline_fraction double precision;
alter table public.pxrd_curves
  add column if not exists stacking_hump_status text;
alter table public.pxrd_curves
  add column if not exists stacking_hump_center_deg double precision;
alter table public.pxrd_curves
  add column if not exists stacking_hump_fwhm_deg double precision;
alter table public.pxrd_curves
  add column if not exists intensity_ratio_100_001 double precision;
alter table public.pxrd_curves
  add column if not exists descriptor_version text;

alter table public.pxrd_curves
  drop constraint if exists pxrd_curves_stacking_hump_status_check;
alter table public.pxrd_curves
  add constraint pxrd_curves_stacking_hump_status_check
  check (stacking_hump_status is null or stacking_hump_status in (
    'hump_detected',      -- pilot 3,385: a hump was fitted in 15-35 deg
    'no_hump_detected',   -- pilot   248: region plotted, no hump. A REAL negative.
    'window_not_covered', -- pilot   182: under 3 deg of 15-35 was plotted. NO DATA.
    'not_computed'        -- pilot     0: no descriptor row exists for this curve
  ));

-- 15-35 deg is the search window (HUMP_LO/HUMP_HI in tools/descriptors.py), so
-- a center outside it is a pipeline bug rather than a wide sample.
alter table public.pxrd_curves
  drop constraint if exists pxrd_curves_descriptor_range_check;
alter table public.pxrd_curves
  add constraint pxrd_curves_descriptor_range_check
  check (
    (crystalline_fraction is null
      or (crystalline_fraction >= 0 and crystalline_fraction <= 1))
    and (stacking_hump_center_deg is null
      or (stacking_hump_center_deg >= 15 and stacking_hump_center_deg <= 35))
    and (stacking_hump_fwhm_deg is null or stacking_hump_fwhm_deg > 0)
    and (intensity_ratio_100_001 is null or intensity_ratio_100_001 >= 0)
  );

-- The constraint that stops "not measured" from ever being stored as "no hump".
-- Hump geometry may only exist when a hump was actually detected, and a curve
-- with no descriptor row may not carry a descriptor number of any kind.
-- Note the asymmetry: a detected hump does NOT guarantee a width or a ratio.
-- 19 pilot curves have a center but no FWHM, and 81 have a hump but no
-- intensity ratio because they have no first peak to take the ratio against.
alter table public.pxrd_curves
  drop constraint if exists pxrd_curves_descriptor_coherence_check;
alter table public.pxrd_curves
  add constraint pxrd_curves_descriptor_coherence_check
  check (
    (stacking_hump_status = 'hump_detected'
      or (stacking_hump_center_deg is null
          and stacking_hump_fwhm_deg is null
          and intensity_ratio_100_001 is null))
    and (stacking_hump_status is distinct from 'not_computed'
      or (crystalline_fraction is null and descriptor_version is null))
  );
-- A NULL stacking_hump_status is a THIRD thing again: a row this importer has
-- not touched since the migration. It is left unconstrained on purpose so the
-- migration can be applied before the import that fills it in.

create index if not exists pxrd_curves_stacking_hump_status_idx
  on public.pxrd_curves (stacking_hump_status);

-- ---------------------------------------------------------------------------
-- 3. The unvetted peak list
-- ---------------------------------------------------------------------------
-- One JSONB array per curve: [{"two_theta":..,"rel_height":..,"prominence":..}]
-- ascending in two_theta. 33,126 entries for the 1,000-paper pilot, ~2.0 MB raw.
--
-- THIS LIST IS UNGATED, and any UI built on it must say so. It comes from
-- tools/build_db.py:
--     find_peaks(y, prominence = 0.03 * range, distance = 0.1 deg)  [top 40]
-- It does NOT pass the SNR / persistence / width / edge gates that produce
-- first_peak_status, and it is a materially different detector. Measured on the
-- pilot: 60.5% of entries have prominence < 0.10; the vetted first peak is
-- absent from this list on 8.0% of curves; 630 curves carry entries BELOW their
-- vetted first peak; only 77.7% of curves have their lowest entry equal to the
-- vetted first peak. Raising the prominence gate makes that agreement worse,
-- not better, so no threshold reconciles the two detectors.
--
-- It is a JSONB column rather than a child table on purpose. The importer's
-- whole idempotency argument is "three upserts, no deletes, uniform key sets".
-- A child table would need a delete pass, because a curve whose peak count
-- falls between runs would otherwise keep its stale rows forever. Replacing one
-- JSONB value is atomic and needs no delete. The search below is server-side
-- either way, which was the reason the recon wanted a table at all.
alter table public.pxrd_curves
  add column if not exists peaks jsonb;
-- The upstream detector keeps the top 40 BY PROMINENCE, so a capped list is not
-- merely short, it is non-monotone in 2-theta: a multi-peak query against it is
-- searching an incomplete list. Surface that as data rather than making every
-- client recount. Pilot: 136 curves are capped.
alter table public.pxrd_curves
  add column if not exists peak_list_truncated boolean not null default false;

alter table public.pxrd_curves
  drop constraint if exists pxrd_curves_peaks_shape_check;
alter table public.pxrd_curves
  add constraint pxrd_curves_peaks_shape_check
  check (peaks is null or jsonb_typeof(peaks) = 'array');

-- ---------------------------------------------------------------------------
-- 4. Server-side multi-peak search
-- ---------------------------------------------------------------------------
-- Returns curves whose UNGATED peak list contains every requested angle within
-- the tolerance, widened per curve to that curve's own axis uncertainty (pilot
-- median 0.046 deg, p95 0.102 deg) so the tolerance is never finer than the
-- axis calibration supports. Selectivity measured on the pilot: one peak at
-- +/-0.1 deg returns 230 of 3,815 curves, two peaks return 26, three return 1.
-- That is why this is an RPC and not a client-side filter.
--
-- tol_deg is clamped into [0.02, 1.0]. The lower clamp bounds the work; the
-- per-curve widening is what actually keeps the query physically honest.
create or replace function public.pxrd_search_peaks(
  targets double precision[],
  tol_deg double precision default 0.15,
  require_status text[] default array['ok']
)
returns table (
  curve_id text,
  matched_two_theta double precision[],
  min_prominence double precision,
  effective_tol_deg double precision,
  peak_list_truncated boolean
)
language sql
stable
security invoker
set search_path = public, pg_temp
as $$
  select
    c.id,
    array_agg(m.two_theta order by m.two_theta),
    min(m.prominence),
    greatest(least(greatest(tol_deg, 0.02), 1.0),
             coalesce(c.two_theta_uncertainty_deg, 0)),
    bool_or(c.peak_list_truncated)
  from public.pxrd_curves c
  cross join lateral unnest(targets) as t(target)
  cross join lateral (
    select
      (entry ->> 'two_theta')::double precision as two_theta,
      (entry ->> 'prominence')::double precision as prominence
    from jsonb_array_elements(c.peaks) as entry
    where abs((entry ->> 'two_theta')::double precision - t.target)
          <= greatest(least(greatest(tol_deg, 0.02), 1.0),
                      coalesce(c.two_theta_uncertainty_deg, 0))
    order by abs((entry ->> 'two_theta')::double precision - t.target)
    limit 1
  ) m
  where c.peaks is not null
    and array_length(targets, 1) between 1 and 8
    and (require_status is null or c.first_peak_status = any (require_status))
  group by c.id, c.two_theta_uncertainty_deg
  having count(distinct t.target) = array_length(targets, 1);
$$;

revoke all on function public.pxrd_search_peaks(
  double precision[], double precision, text[]) from public;
grant execute on function public.pxrd_search_peaks(
  double precision[], double precision, text[]) to anon, authenticated;

-- ---------------------------------------------------------------------------
-- 5. Cross-paper material LABEL groups
-- ---------------------------------------------------------------------------
-- Groups curves whose published material labels normalise to the same string.
-- This is a claim about LABEL AGREEMENT, not about verified material identity,
-- and every column here is named to keep that distinction.
--
-- Provenance: public.pxrd_curves.material_name only. No third-party dataset is
-- read, copied, or derived from here. The local peter_match / peter_cof tables
-- are not consulted: peter_match.peter_row is DOI-gated and so cannot express
-- cross-paper identity at all (all 475 tier-A pilot groups span one paper), and
-- matched_name - its only cross-paper field - is a copy of a third-party
-- content column that this project may not redistribute.
create table if not exists public.pxrd_material_groups (
  id             text primary key,             -- = group_key, stable across imports
  group_key      text not null unique,         -- normalised label, e.g. 'tppa1'
  display_name   text not null,                -- most frequent RAW label, e.g. 'TpPa-1'
  label_variants text[] not null default '{}', -- every raw label folded in
  paper_count    integer not null default 0,
  curve_count    integer not null default 0,
  match_basis    text not null default 'label_normalised',
  specificity    text not null default 'specific',
  updated_at     timestamptz not null default now()
);

-- Both match_basis values are label agreement. Neither asserts that the
-- materials are the same, and there is deliberately no score or star rating:
-- there is no calibrated probability behind this, and inventing one would be
-- the overclaim the gate exists to prevent.
alter table public.pxrd_material_groups
  drop constraint if exists pxrd_material_groups_match_basis_check;
alter table public.pxrd_material_groups
  add constraint pxrd_material_groups_match_basis_check
  check (match_basis in ('label_identical', 'label_normalised'));

-- specificity is the publish gate, and it is the single most important honesty
-- decision in this feature. The LARGEST label groups are the least meaningful:
-- in the pilot the biggest is the literal word "COF" across 26 papers, and
-- COF-1..COF-5 are per-paper serial numbering. Only 'specific' groups may be
-- rendered. The others are stored so the refusals stay inspectable, but no
-- curve is ever allowed to point at one (see the FK guard in section 6).
alter table public.pxrd_material_groups
  drop constraint if exists pxrd_material_groups_specificity_check;
alter table public.pxrd_material_groups
  add constraint pxrd_material_groups_specificity_check
  check (specificity in ('specific', 'paper_local', 'serial', 'generic'));

alter table public.pxrd_material_groups
  drop constraint if exists pxrd_material_groups_counts_check;
alter table public.pxrd_material_groups
  add constraint pxrd_material_groups_counts_check
  check (paper_count >= 0
         and curve_count >= 0
         and curve_count >= paper_count
         and cardinality(label_variants) >= 1);

-- Pilot: 42 groups qualify, covering 110 papers and 174 curves.
create index if not exists pxrd_material_groups_publishable_idx
  on public.pxrd_material_groups (paper_count desc)
  where specificity = 'specific' and paper_count > 1;

alter table public.pxrd_material_groups enable row level security;

drop policy if exists pxrd_material_groups_read on public.pxrd_material_groups;
create policy pxrd_material_groups_read
  on public.pxrd_material_groups for select to anon, authenticated using (true);

grant select on public.pxrd_material_groups to anon, authenticated;

-- ---------------------------------------------------------------------------
-- 6. Curve -> label group link
-- ---------------------------------------------------------------------------
-- NULL means "this curve is not linked". It never means "unique material", and
-- the empty state in the UI must say so.
alter table public.pxrd_curves
  add column if not exists material_group_id text;

alter table public.pxrd_curves
  drop constraint if exists pxrd_curves_material_group_fk;
alter table public.pxrd_curves
  add constraint pxrd_curves_material_group_fk
  foreign key (material_group_id)
  references public.pxrd_material_groups (id) on delete set null;

create index if not exists pxrd_curves_material_group_idx
  on public.pxrd_curves (material_group_id)
  where material_group_id is not null;

-- ---------------------------------------------------------------------------
-- 7. "Nobody has checked this paper" is not "this paper is fine"
-- ---------------------------------------------------------------------------
-- 20260828 declared publication_status `not null default 'active'`, which makes
-- "never checked" unrepresentable: a row created before Crossref was consulted
-- asserts a clean bill of health nobody earned. Add the missing value, add the
-- evidence column that makes the distinction durable, and re-stamp the rows
-- that were only ever defaulted.
-- ORDER MATTERS HERE, and getting it wrong aborts the migration.
-- `add constraint ... check` validates every existing row immediately, so the
-- evidence constraint cannot be added until the rows that violate it have been
-- re-stamped, and the re-stamp cannot run until the CHECK permits the value it
-- writes. Hence: column, then value list, then default, then UPDATE, then the
-- evidence constraint.
alter table public.pxrd_papers
  add column if not exists publication_status_checked_at timestamptz;

alter table public.pxrd_papers
  drop constraint if exists pxrd_papers_publication_status_check;
alter table public.pxrd_papers
  add constraint pxrd_papers_publication_status_check
  check (publication_status in
    ('unchecked', 'active', 'retracted', 'withdrawn', 'concern', 'corrected'));

alter table public.pxrd_papers
  alter column publication_status set default 'unchecked';

-- Every 'active' with no recorded check is a row 20260828 defaulted, not a
-- verdict anybody reached: that migration declared the column
-- `not null default 'active'`, so it stamped a clean bill of health on all
-- 1,000 live papers before Crossref was ever consulted. Demote exactly those.
--
-- Re-runnable in both directions. A row this touches becomes 'unchecked' and no
-- longer matches the first clause. A row the importer later publishes as a
-- genuine 'active' always carries publication_status_checked_at, because the
-- constraint below forces the importer to send them together, so it is excluded
-- by the second clause. The UPDATE can never claw back a real verdict.
update public.pxrd_papers
set publication_status = 'unchecked', updated_at = now()
where publication_status = 'active'
  and publication_status_checked_at is null;

-- 'unchecked' means exactly "no Crossref lookup has been recorded", and
-- publication_status_checked_at is the evidence for every other value.
alter table public.pxrd_papers
  drop constraint if exists pxrd_papers_publication_status_evidence_check;
alter table public.pxrd_papers
  add constraint pxrd_papers_publication_status_evidence_check
  check ((publication_status = 'unchecked')
         = (publication_status_checked_at is null));

commit;

-- If the LAST statement fails with "check constraint ... is violated by some
-- row", the table holds a non-'active' status - a retraction or a correction -
-- with no recorded check. Deliberately not repaired here: back-dating a
-- Crossref lookup that never happened is the exact fabrication this section
-- exists to prevent, and demoting the row would drop a real retraction flag.
-- Run
--   python3 scripts/backfill_crossref_metadata.py --all-papers --limit 2370 \
--     --refresh --apply
--   python3 -u scripts/import_pxrd_to_supabase.py --limit 1000 --workers 8
-- which republishes those rows with their evidence, then re-run this file.

-- If section 4 fails with "cannot change return type of existing function",
-- an earlier draft is installed. Run
--   drop function if exists public.pxrd_search_peaks(
--     double precision[], double precision, text[]);
-- and then re-run this file.

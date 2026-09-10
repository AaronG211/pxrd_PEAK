-- Open PXRD Database
-- Non-destructive schema creation. Run reset.sql first only when intentionally
-- replacing the legacy COF database.
--
-- This file is the definition for a FRESH deployment. An already-deployed
-- database is brought to the same shape by the files in ./migrations, because
-- `create table if not exists` never adds a column to an existing table.

create table if not exists public.pxrd_papers (
  id text primary key,
  paper_number text not null unique,
  doi text unique,
  title text not null,
  authors text,
  journal text,
  publication_year integer check (publication_year between 1800 and 2200),
  source_url text,
  -- Publication lifecycle, from Crossref. 'unchecked' until a lookup happens,
  -- then 'active' unless a retraction, withdrawal, expression of concern, or
  -- correction is detected. 'unchecked' and 'active' are DIFFERENT claims: the
  -- default must never assert a clean bill of health nobody earned.
  publication_status text not null default 'unchecked'
    constraint pxrd_papers_publication_status_check
    check (publication_status in
      ('unchecked', 'active', 'retracted', 'withdrawn', 'concern', 'corrected')),
  publication_status_notice_doi text,
  publication_status_source text
    constraint pxrd_papers_publication_status_source_check
    check (publication_status_source is null or publication_status_source in
      ('crossref-update', 'crossref-title', 'manual')),
  publication_status_updated date,
  -- The evidence that a lookup happened at all. Tied to the status below so
  -- "nobody has checked" can never be stored as "checked and fine".
  publication_status_checked_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint pxrd_papers_publication_status_evidence_check check (
    (publication_status = 'unchecked')
    = (publication_status_checked_at is null)
  )
);

create table if not exists public.pxrd_figures (
  id text primary key,
  paper_id text not null references public.pxrd_papers(id) on delete cascade,
  figure_label text,
  page_number integer check (page_number is null or page_number > 0),
  caption text,
  crop_path text not null,
  digitized_plot_path text not null,
  overlay_path text,
  -- Derived by the importer, never asserted. 'flagged' means an automated
  -- check disputed the figure; 'pending' means nothing did.
  quality_status text not null default 'pending'
    constraint pxrd_figures_quality_status_check
    check (quality_status in ('pending', 'flagged')),
  -- How the 2-theta axis calibration was established. The pipeline fits the
  -- axis twice, from detected tick marks and from OCR of the axis labels.
  verification_status text not null default 'axis_unverified'
    constraint pxrd_figures_verification_status_check
    check (verification_status in (
      'axis_cross_validated',
      'axis_single_method',
      'axis_arbitrated',
      'axis_unverified'
    )),
  axis_agreement_deg double precision,
  axis_rmse_deg double precision,
  axis_tick_count integer,
  series_detected integer,
  series_digitized integer,
  -- Computed traces (simulated / Pawley-refined / difference) on this figure
  -- that are excluded by policy. A disclosure, not a defect.
  series_omitted_computed integer not null default 0,
  -- Curves on this figure excluded by measurement: they failed the offline
  -- shape filter, or the figure's axis was never cross-validated. Counted
  -- apart from the line above because "we chose not to publish a simulation"
  -- and "this trace did not pass" are different facts.
  series_excluded_quality integer not null default 0,
  sort_order integer not null default 0,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint pxrd_figures_verification_range_check check (
    (axis_agreement_deg is null or axis_agreement_deg >= 0)
    and (axis_rmse_deg is null or axis_rmse_deg >= 0)
    and (axis_tick_count is null or axis_tick_count >= 0)
    and (series_detected is null or series_detected >= 0)
    and (series_digitized is null or series_digitized >= 0)
    and series_omitted_computed >= 0
    and series_excluded_quality >= 0
  )
);

-- Cross-paper material LABEL groups. Curves are grouped when their published
-- material labels normalise to the same string: a claim about LABEL AGREEMENT,
-- never about verified material identity. Provenance is
-- pxrd_curves.material_name only; no third-party dataset is read or derived
-- from here. See scripts/material_labels.py.
create table if not exists public.pxrd_material_groups (
  id             text primary key,             -- = group_key, stable across imports
  group_key      text not null unique,         -- normalised label, e.g. 'tppa1'
  display_name   text not null,                -- most frequent RAW label, e.g. 'TpPa-1'
  label_variants text[] not null default '{}', -- every raw label folded in
  paper_count    integer not null default 0,
  curve_count    integer not null default 0,
  -- Both values are label agreement. There is deliberately no score: no
  -- calibrated probability exists behind this, and inventing one would be the
  -- overclaim the specificity gate exists to prevent.
  match_basis    text not null default 'label_normalised'
    constraint pxrd_material_groups_match_basis_check
    check (match_basis in ('label_identical', 'label_normalised')),
  -- The publish gate. Only 'specific' groups may be rendered; 'generic'
  -- ("COF"), 'serial' ("COF-1") and 'paper_local' ("M-COF") are stored so the
  -- refusals stay inspectable but never link anything.
  specificity    text not null default 'specific'
    constraint pxrd_material_groups_specificity_check
    check (specificity in ('specific', 'paper_local', 'serial', 'generic')),
  updated_at     timestamptz not null default now(),
  constraint pxrd_material_groups_counts_check check (
    paper_count >= 0
    and curve_count >= 0
    and curve_count >= paper_count
    and cardinality(label_variants) >= 1
  )
);

create table if not exists public.pxrd_curves (
  id text primary key,
  figure_id text not null references public.pxrd_figures(id) on delete cascade,
  series_id text not null unique,
  label text not null,
  material_name text,
  curve_role text not null default 'unclassified'
    check (curve_role in ('experimental', 'simulated', 'refined', 'difference', 'reference', 'unclassified')),
  sample_state text,
  two_theta_min double precision,
  two_theta_max double precision,
  point_count integer not null default 0 check (point_count >= 0),
  peak_count integer check (peak_count is null or peak_count >= 0),
  data_path text,
  in_clean_set boolean not null default false,
  -- How this curve earned its place. 'clean' is the original corpus definition
  -- (in_clean_set). 'axis_verified' is a curve that figure-level quarantine
  -- excluded only because a SIBLING trace in the same panel was flagged, and
  -- whose figure's 2-theta axis was independently cross-validated - the direct
  -- measurement of the panel-misread risk that quarantine was standing in for.
  -- See tools/admit_axis_verified.py and migrations/20260910_curve_admission.sql.
  admission text not null default 'clean'
    constraint pxrd_curves_admission_check
    check (admission in ('clean', 'axis_verified')),
  constraint pxrd_curves_admission_clean_set_check
    check (in_clean_set = (admission = 'clean')),
  -- Per-curve automated fidelity numbers, carried from the extraction
  -- pipeline. two_theta_uncertainty_deg is the number a reuser needs.
  two_theta_uncertainty_deg double precision,
  trace_confidence double precision,
  snap_rate double precision,
  mean_snap_residual_px double precision,
  -- First-peak geometry. first_peak_snr is deliberately NOT published:
  -- tools/first_peak.py floors sigma at 1e-3 * range, so SNR saturates at 1000
  -- (pilot median 966). It measures how smooth our own vectorization was, not
  -- signal quality.
  first_peak_two_theta_deg double precision,
  -- DERIVED, not measured: d = lambda / (2 sin(theta)) with the wavelength
  -- below. Filtering by d is filtering by 2-theta under a unit relabel.
  first_peak_d_angstrom double precision,
  first_peak_fwhm_deg double precision,
  first_peak_status text
    constraint pxrd_curves_first_peak_status_check
    check (first_peak_status is null or first_peak_status in (
      'ok', 'low_confidence', 'truncated_at_window_start', 'no_bragg_peak')),
  -- The wavelength the d-spacing was computed from, and where it came from.
  -- 'assumed_cu_ka' (1.5406 A) for every curve in the present corpus.
  first_peak_wavelength_angstrom double precision,
  first_peak_wavelength_source text
    constraint pxrd_curves_first_peak_wavelength_source_check
    check (first_peak_wavelength_source is null
           or first_peak_wavelength_source in ('assumed_cu_ka', 'paper_reported')),
  -- Crystallinity descriptors. crystalline_fraction is
  -- sum(y - rolling 10th-pct baseline over 4 deg) / sum(y - min(y)): the
  -- fraction of intensity in features narrower than ~4 deg, NOT degree of
  -- crystallinity, and strongly confounded by the plotted 2-theta span. Read it
  -- beside two_theta_min / two_theta_max. stacking_hump_height is omitted: it is
  -- in un-normalized units and is not comparable between curves.
  crystalline_fraction double precision,
  stacking_hump_status text
    constraint pxrd_curves_stacking_hump_status_check
    check (stacking_hump_status is null or stacking_hump_status in (
      'hump_detected', 'no_hump_detected', 'window_not_covered', 'not_computed')),
  stacking_hump_center_deg double precision,
  stacking_hump_fwhm_deg double precision,
  intensity_ratio_100_001 double precision,
  descriptor_version text,
  -- The UNGATED automatic peak list, ascending in two_theta:
  -- [{"two_theta":..,"rel_height":..,"prominence":..}]. It passes none of the
  -- physics gates behind first_peak_status. Any UI built on it must say so.
  peaks jsonb,
  peak_list_truncated boolean not null default false,
  -- NULL means "not linked", never "unique material".
  material_group_id text references public.pxrd_material_groups(id) on delete set null,
  sort_order integer not null default 0,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  check (
    two_theta_min is null
    or two_theta_max is null
    or two_theta_min <= two_theta_max
  ),
  constraint pxrd_curves_fidelity_range_check check (
    (two_theta_uncertainty_deg is null or two_theta_uncertainty_deg >= 0)
    and (trace_confidence is null or (trace_confidence >= 0 and trace_confidence <= 1))
    and (snap_rate is null or (snap_rate >= 0 and snap_rate <= 1))
    and (mean_snap_residual_px is null or mean_snap_residual_px >= 0)
  ),
  constraint pxrd_curves_first_peak_range_check check (
    (first_peak_two_theta_deg is null
      or (first_peak_two_theta_deg > 0 and first_peak_two_theta_deg < 180))
    and (first_peak_d_angstrom is null or first_peak_d_angstrom > 0)
    and (first_peak_fwhm_deg is null
      or (first_peak_fwhm_deg >= 0.05 and first_peak_fwhm_deg <= 3.0))
    and (first_peak_wavelength_angstrom is null
      or (first_peak_wavelength_angstrom > 0.4
          and first_peak_wavelength_angstrom < 3.0))
  ),
  constraint pxrd_curves_first_peak_coherence_check check (
    (first_peak_two_theta_deg is null) = (first_peak_d_angstrom is null)
    and (first_peak_two_theta_deg is null) = (first_peak_fwhm_deg is null)
    and (first_peak_two_theta_deg is null)
        = (first_peak_wavelength_angstrom is null)
    and (first_peak_wavelength_angstrom is null)
        = (first_peak_wavelength_source is null)
    and (first_peak_status not in ('truncated_at_window_start', 'no_bragg_peak')
         or first_peak_two_theta_deg is null)
  ),
  constraint pxrd_curves_descriptor_range_check check (
    (crystalline_fraction is null
      or (crystalline_fraction >= 0 and crystalline_fraction <= 1))
    and (stacking_hump_center_deg is null
      or (stacking_hump_center_deg >= 15 and stacking_hump_center_deg <= 35))
    and (stacking_hump_fwhm_deg is null or stacking_hump_fwhm_deg > 0)
    and (intensity_ratio_100_001 is null or intensity_ratio_100_001 >= 0)
  ),
  -- Stops "not measured" from ever being stored as "no hump".
  constraint pxrd_curves_descriptor_coherence_check check (
    (stacking_hump_status = 'hump_detected'
      or (stacking_hump_center_deg is null
          and stacking_hump_fwhm_deg is null
          and intensity_ratio_100_001 is null))
    and (stacking_hump_status is distinct from 'not_computed'
      or (crystalline_fraction is null and descriptor_version is null))
  ),
  constraint pxrd_curves_peaks_shape_check check (
    peaks is null or jsonb_typeof(peaks) = 'array'
  )
);

create index if not exists pxrd_figures_paper_id_idx
  on public.pxrd_figures (paper_id, sort_order);
create index if not exists pxrd_curves_figure_id_idx
  on public.pxrd_curves (figure_id, sort_order);
create index if not exists pxrd_papers_title_idx
  on public.pxrd_papers using gin (to_tsvector('english', title));
create index if not exists pxrd_figures_verification_status_idx
  on public.pxrd_figures (verification_status);
-- 13 of 2,370 corpus papers are non-active, so a partial index is enough.
create index if not exists pxrd_papers_publication_status_idx
  on public.pxrd_papers (publication_status)
  where publication_status <> 'active';
-- 70.4% of published curves are first_peak_status 'ok'; the search gates on it.
create index if not exists pxrd_curves_first_peak_search_idx
  on public.pxrd_curves (first_peak_status, first_peak_two_theta_deg);
create index if not exists pxrd_curves_first_peak_d_idx
  on public.pxrd_curves (first_peak_d_angstrom)
  where first_peak_d_angstrom is not null;
create index if not exists pxrd_curves_stacking_hump_status_idx
  on public.pxrd_curves (stacking_hump_status);
create index if not exists pxrd_curves_material_group_idx
  on public.pxrd_curves (material_group_id)
  where material_group_id is not null;
create index if not exists pxrd_material_groups_publishable_idx
  on public.pxrd_material_groups (paper_count desc)
  where specificity = 'specific' and paper_count > 1;

-- Multi-peak search over the UNGATED peak list, server-side because it is
-- highly selective: one peak at +/-0.1 deg returns 230 of 3,815 pilot curves,
-- two return 26, three return 1. The tolerance is widened per curve to that
-- curve's own axis uncertainty so it is never finer than the calibration
-- supports, and tol_deg is clamped into [0.02, 1.0] to bound the work.
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
  -- What the default browse filter needs. curve_count is every published curve;
  -- a paper with clean_curve_count = 0 has a working page but is reachable from
  -- the index only with re-admitted curves included.
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

alter table public.pxrd_papers enable row level security;
alter table public.pxrd_figures enable row level security;
alter table public.pxrd_curves enable row level security;
alter table public.pxrd_material_groups enable row level security;

drop policy if exists "Public can read PXRD material groups" on public.pxrd_material_groups;
create policy "Public can read PXRD material groups"
  on public.pxrd_material_groups for select
  to anon, authenticated
  using (true);

drop policy if exists "Public can read PXRD papers" on public.pxrd_papers;
create policy "Public can read PXRD papers"
  on public.pxrd_papers for select
  to anon, authenticated
  using (true);

drop policy if exists "Public can read PXRD figures" on public.pxrd_figures;
create policy "Public can read PXRD figures"
  on public.pxrd_figures for select
  to anon, authenticated
  using (true);

drop policy if exists "Public can read PXRD curves" on public.pxrd_curves;
create policy "Public can read PXRD curves"
  on public.pxrd_curves for select
  to anon, authenticated
  using (true);

grant select on public.pxrd_paper_index to anon, authenticated;
grant select on public.pxrd_material_groups to anon, authenticated;

insert into storage.buckets (id, name, public)
values ('pxrd-assets', 'pxrd-assets', true)
on conflict (id) do update set public = excluded.public;

drop policy if exists "Public can read PXRD assets" on storage.objects;
create policy "Public can read PXRD assets"
  on storage.objects for select
  to anon, authenticated
  using (bucket_id = 'pxrd-assets');

-- No public INSERT, UPDATE, or DELETE policies are created. Curated ingestion
-- uses the secret key only from a trusted local process.

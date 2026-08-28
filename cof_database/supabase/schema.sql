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
  -- Publication lifecycle, from Crossref. 'active' until a retraction,
  -- withdrawal, expression of concern, or correction is detected.
  publication_status text not null default 'active'
    constraint pxrd_papers_publication_status_check
    check (publication_status in
      ('active', 'retracted', 'withdrawn', 'concern', 'corrected')),
  publication_status_notice_doi text,
  publication_status_source text
    constraint pxrd_papers_publication_status_source_check
    check (publication_status_source is null or publication_status_source in
      ('crossref-update', 'crossref-title', 'manual')),
  publication_status_updated date,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
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
  -- 'reviewed' is RESERVED for a human-review workflow that does not exist.
  -- No human has reviewed any figure in this database, so the importer never
  -- emits it. 'flagged' means an automated check disputed the figure;
  -- 'pending' means automated extraction passed its own checks and nothing
  -- has reviewed it since.
  quality_status text not null default 'pending'
    check (quality_status in ('reviewed', 'pending', 'flagged')),
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
  series_omitted_computed integer not null default 0,
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
  -- Per-curve automated fidelity numbers, carried from the extraction
  -- pipeline. two_theta_uncertainty_deg is the number a reuser needs.
  two_theta_uncertainty_deg double precision,
  trace_confidence double precision,
  snap_rate double precision,
  mean_snap_residual_px double precision,
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

alter table public.pxrd_papers enable row level security;
alter table public.pxrd_figures enable row level security;
alter table public.pxrd_curves enable row level security;

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

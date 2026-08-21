-- Open PXRD Database
-- Non-destructive schema creation. Run reset.sql first only when intentionally
-- replacing the legacy COF database.

create table if not exists public.pxrd_papers (
  id text primary key,
  paper_number text not null unique,
  doi text unique,
  title text not null,
  authors text,
  journal text,
  publication_year integer check (publication_year between 1800 and 2200),
  source_url text,
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
  quality_status text not null default 'pending'
    check (quality_status in ('reviewed', 'pending', 'flagged')),
  sort_order integer not null default 0,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
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
  sort_order integer not null default 0,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  check (
    two_theta_min is null
    or two_theta_max is null
    or two_theta_min <= two_theta_max
  )
);

create index if not exists pxrd_figures_paper_id_idx
  on public.pxrd_figures (paper_id, sort_order);
create index if not exists pxrd_curves_figure_id_idx
  on public.pxrd_curves (figure_id, sort_order);
create index if not exists pxrd_papers_title_idx
  on public.pxrd_papers using gin (to_tsvector('english', title));

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
  count(distinct c.material_name)::integer as material_count
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

-- Open PXRD private digitization workspace (Phase A)
--
-- Run after schema.sql. This adds authenticated, user-owned draft jobs and
-- private file storage. It deliberately does not add a worker, model key, or
-- any path from user jobs into the public curated PXRD tables.

begin;

create table if not exists public.digitization_jobs (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null default auth.uid()
    references auth.users(id) on delete cascade,
  status text not null default 'draft'
    check (status in (
      'draft',
      'saved',
      'awaiting_quote',
      'awaiting_approval',
      'queued',
      'running',
      'review',
      'completed',
      'failed',
      'cancelled'
    )),
  source_file_count integer not null
    check (source_file_count between 1 and 10),
  source_total_bytes bigint not null
    check (source_total_bytes > 0 and source_total_bytes <= 209715200),
  client_page_count integer not null
    check (client_page_count > 0),
  client_figure_count integer not null
    check (client_figure_count >= 0),
  budget_cap_usd numeric(10, 4) not null
    check (budget_cap_usd > 0 and budget_cap_usd <= 1000),
  client_estimate_usd numeric(10, 4) not null
    check (client_estimate_usd >= 0),
  client_estimate_upper_usd numeric(10, 4) not null
    check (
      client_estimate_upper_usd >= client_estimate_usd
      and client_estimate_upper_usd <= budget_cap_usd
    ),
  server_estimate_usd numeric(10, 4)
    check (server_estimate_usd is null or server_estimate_usd >= 0),
  server_estimate_upper_usd numeric(10, 4)
    check (server_estimate_upper_usd is null or server_estimate_upper_usd >= 0),
  actual_cost_usd numeric(10, 4) not null default 0
    check (actual_cost_usd >= 0),
  cancel_requested boolean not null default false,
  error_summary text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  submitted_at timestamptz,
  started_at timestamptz,
  completed_at timestamptz,
  unique (id, user_id)
);

-- Keep the migration safe to rerun over the earlier Phase-A draft. PostgreSQL
-- does not replace CHECK constraints when CREATE TABLE IF NOT EXISTS finds an
-- existing table, so explicitly retire the old worker-ready state before
-- installing the current planning-only state machine.
alter table public.digitization_jobs
  drop constraint if exists digitization_jobs_status_check;

update public.digitization_jobs
set status = 'saved',
    updated_at = now()
where status = 'awaiting_worker';

alter table public.digitization_jobs
  add constraint digitization_jobs_status_check
  check (status in (
    'draft',
    'saved',
    'awaiting_quote',
    'awaiting_approval',
    'queued',
    'running',
    'review',
    'completed',
    'failed',
    'cancelled'
  ));

create table if not exists public.digitization_job_files (
  id uuid primary key default gen_random_uuid(),
  job_id uuid not null,
  user_id uuid not null default auth.uid(),
  file_role text not null check (file_role in ('input', 'result')),
  bucket_id text not null,
  storage_path text not null,
  original_name text,
  mime_type text,
  size_bytes bigint check (size_bytes is null or size_bytes >= 0),
  created_at timestamptz not null default now(),
  foreign key (job_id, user_id)
    references public.digitization_jobs(id, user_id)
    on delete cascade,
  unique (bucket_id, storage_path),
  check (
    (file_role = 'input' and bucket_id = 'pxrd-user-uploads')
    or
    (file_role = 'result' and bucket_id = 'pxrd-user-results')
  ),
  check (storage_path like user_id::text || '/' || job_id::text || '/%')
);

create index if not exists digitization_jobs_user_created_idx
  on public.digitization_jobs (user_id, created_at desc);
create index if not exists digitization_job_files_job_idx
  on public.digitization_job_files (job_id, file_role);

alter table public.digitization_jobs enable row level security;
alter table public.digitization_job_files enable row level security;

revoke all on public.digitization_jobs from anon, authenticated;
revoke all on public.digitization_job_files from anon, authenticated;
grant select on public.digitization_jobs to authenticated;
grant select on public.digitization_job_files to authenticated;

drop policy if exists "Users read own digitization jobs" on public.digitization_jobs;
create policy "Users read own digitization jobs"
  on public.digitization_jobs for select
  to authenticated
  using (user_id = (select auth.uid()));

drop policy if exists "Users create safe draft jobs" on public.digitization_jobs;
drop policy if exists "Users delete empty draft jobs" on public.digitization_jobs;

drop policy if exists "Users read own job files" on public.digitization_job_files;
create policy "Users read own job files"
  on public.digitization_job_files for select
  to authenticated
  using (user_id = (select auth.uid()));

drop policy if exists "Users register own draft inputs" on public.digitization_job_files;
drop policy if exists "Users delete own draft input manifests" on public.digitization_job_files;

insert into storage.buckets
  (id, name, public, file_size_limit, allowed_mime_types)
values
  (
    'pxrd-user-uploads',
    'pxrd-user-uploads',
    false,
    20971520,
    array['application/pdf']
  ),
  (
    'pxrd-user-results',
    'pxrd-user-results',
    false,
    52428800,
    array[
      'image/png',
      'image/webp',
      'text/csv',
      'text/plain',
      'application/json',
      'application/zip',
      'application/octet-stream'
    ]
  )
on conflict (id) do update
set public = excluded.public,
    file_size_limit = excluded.file_size_limit,
    allowed_mime_types = excluded.allowed_mime_types;

-- Storage INSERT policies cannot safely count their own table under RLS. This
-- helper is intentionally boolean-only and uses the job's declared file count
-- to bound direct client uploads. Google OAuth must remain test-user-only for
-- Phase A; a public launch still needs rate limiting and retention cleanup.
create or replace function public.can_upload_digitization_input(p_job_id uuid)
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
  select exists (
    select 1
    from public.digitization_jobs j
    where j.id = p_job_id
      and j.user_id = auth.uid()
      and j.status = 'draft'
      and (
        select count(*)
        from storage.objects o
        where o.bucket_id = 'pxrd-user-uploads'
          and o.name like j.user_id::text || '/' || j.id::text || '/%'
      ) < j.source_file_count
  );
$$;

revoke all on function public.can_upload_digitization_input(uuid) from public;
grant execute on function public.can_upload_digitization_input(uuid) to authenticated;

drop policy if exists "Users read own uploaded inputs" on storage.objects;
create policy "Users read own uploaded inputs"
  on storage.objects for select
  to authenticated
  using (
    bucket_id = 'pxrd-user-uploads'
    and (storage.foldername(name))[1] = (select auth.uid())::text
    and exists (
      select 1
      from public.digitization_jobs j
      where j.id::text = (storage.foldername(name))[2]
        and j.user_id = (select auth.uid())
    )
  );

drop policy if exists "Users upload to own draft jobs" on storage.objects;
create policy "Users upload to own draft jobs"
  on storage.objects for insert
  to authenticated
  with check (
    bucket_id = 'pxrd-user-uploads'
    and (storage.foldername(name))[1] = (select auth.uid())::text
    and public.can_upload_digitization_input(
      ((storage.foldername(name))[2])::uuid
    )
  );

drop policy if exists "Users delete own draft uploads" on storage.objects;
create policy "Users delete own draft uploads"
  on storage.objects for delete
  to authenticated
  using (
    bucket_id = 'pxrd-user-uploads'
    and (storage.foldername(name))[1] = (select auth.uid())::text
    and exists (
      select 1
      from public.digitization_jobs j
      where j.id::text = (storage.foldername(name))[2]
        and j.user_id = (select auth.uid())
        and j.status in ('draft', 'saved')
    )
  );

drop policy if exists "Users read own digitization results" on storage.objects;
create policy "Users read own digitization results"
  on storage.objects for select
  to authenticated
  using (
    bucket_id = 'pxrd-user-results'
    and (storage.foldername(name))[1] = (select auth.uid())::text
    and exists (
      select 1
      from public.digitization_jobs j
      where j.id::text = (storage.foldername(name))[2]
        and j.user_id = (select auth.uid())
    )
  );

-- All private writes go through narrowly scoped RPCs. This prevents the browser
-- from supplying IDs, ownership, timestamps, server estimates, or job status.
create or replace function public.create_digitization_job(
  p_source_file_count integer,
  p_source_total_bytes bigint,
  p_client_page_count integer,
  p_client_figure_count integer,
  p_budget_cap_usd numeric,
  p_client_estimate_usd numeric,
  p_client_estimate_upper_usd numeric
)
returns public.digitization_jobs
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_user_id uuid := auth.uid();
  v_active_jobs integer;
  v_active_bytes bigint;
  v_job public.digitization_jobs%rowtype;
begin
  if v_user_id is null then
    raise exception 'Authentication required';
  end if;
  if p_source_file_count not between 1 and 10
     or p_source_total_bytes <= 0
     or p_source_total_bytes > 209715200
     or p_client_page_count <= 0
     or p_client_figure_count < 0
     or p_budget_cap_usd <= 0
     or p_budget_cap_usd > 1000
     or p_client_estimate_usd < 0
     or p_client_estimate_upper_usd < p_client_estimate_usd
     or p_client_estimate_upper_usd > p_budget_cap_usd then
    raise exception 'Invalid job planning values';
  end if;

  perform pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended(v_user_id::text, 0)
  );

  select count(*), coalesce(sum(source_total_bytes), 0)
  into v_active_jobs, v_active_bytes
  from public.digitization_jobs
  where user_id = v_user_id
    and status in (
      'draft', 'saved', 'awaiting_quote', 'awaiting_approval',
      'queued', 'running', 'review'
    );

  if v_active_jobs >= 2 then
    raise exception 'Private beta limit: delete an existing saved job before creating another';
  end if;
  if v_active_bytes + p_source_total_bytes > 419430400 then
    raise exception 'Private beta storage limit exceeded';
  end if;

  insert into public.digitization_jobs (
    user_id,
    source_file_count,
    source_total_bytes,
    client_page_count,
    client_figure_count,
    budget_cap_usd,
    client_estimate_usd,
    client_estimate_upper_usd
  ) values (
    v_user_id,
    p_source_file_count,
    p_source_total_bytes,
    p_client_page_count,
    p_client_figure_count,
    p_budget_cap_usd,
    p_client_estimate_usd,
    p_client_estimate_upper_usd
  )
  returning * into v_job;

  return v_job;
end;
$$;

create or replace function public.register_digitization_input(
  p_job_id uuid,
  p_storage_path text,
  p_original_name text,
  p_size_bytes bigint
)
returns uuid
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_user_id uuid := auth.uid();
  v_expected_count integer;
  v_registered_count integer;
  v_storage_size bigint;
  v_file_id uuid;
begin
  select source_file_count into v_expected_count
  from public.digitization_jobs
  where id = p_job_id
    and user_id = v_user_id
    and status = 'draft'
  for update;

  if not found then
    raise exception 'Draft job not found or not owned by the current user';
  end if;
  if p_storage_path not like v_user_id::text || '/' || p_job_id::text || '/%'
     or p_size_bytes <= 0
     or p_size_bytes > 20971520 then
    raise exception 'Invalid uploaded input metadata';
  end if;

  select nullif(o.metadata ->> 'size', '')::bigint into v_storage_size
  from storage.objects o
  where o.bucket_id = 'pxrd-user-uploads'
    and o.name = p_storage_path
    and o.owner_id = v_user_id::text;

  if not found then
    raise exception 'Uploaded input object not found';
  end if;
  if v_storage_size is not null and v_storage_size <> p_size_bytes then
    raise exception 'Uploaded input size does not match its manifest';
  end if;

  select count(*) into v_registered_count
  from public.digitization_job_files
  where job_id = p_job_id and file_role = 'input';

  if v_registered_count >= v_expected_count then
    raise exception 'The declared file count has already been reached';
  end if;

  insert into public.digitization_job_files (
    job_id, user_id, file_role, bucket_id, storage_path,
    original_name, mime_type, size_bytes
  ) values (
    p_job_id, v_user_id, 'input', 'pxrd-user-uploads', p_storage_path,
    left(p_original_name, 500), 'application/pdf', p_size_bytes
  )
  returning id into v_file_id;

  return v_file_id;
end;
$$;

create or replace function public.save_digitization_job(p_job_id uuid)
returns public.digitization_jobs
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_job public.digitization_jobs%rowtype;
  v_manifest_count integer;
  v_stored_count integer;
  v_manifest_bytes bigint;
begin
  select * into v_job
  from public.digitization_jobs
  where id = p_job_id
    and user_id = auth.uid()
    and status = 'draft'
  for update;

  if not found then
    raise exception 'Draft job not found or not owned by the current user';
  end if;

  select count(*), coalesce(sum(size_bytes), 0)
  into v_manifest_count, v_manifest_bytes
  from public.digitization_job_files
  where job_id = p_job_id
    and user_id = auth.uid()
    and file_role = 'input';

  select count(*) into v_stored_count
  from public.digitization_job_files f
  join storage.objects o
    on o.bucket_id = f.bucket_id
   and o.name = f.storage_path
  where f.job_id = p_job_id
    and f.user_id = auth.uid()
    and f.file_role = 'input';

  if v_manifest_count <> v_job.source_file_count
     or v_stored_count <> v_job.source_file_count
     or v_manifest_bytes <> v_job.source_total_bytes then
    raise exception 'Every declared input must be uploaded before saving';
  end if;

  update public.digitization_jobs
  set status = 'saved',
      submitted_at = now(),
      updated_at = now()
  where id = p_job_id
  returning * into v_job;

  return v_job;
end;
$$;

create or replace function public.delete_digitization_job_record(p_job_id uuid)
returns void
language plpgsql
security definer
set search_path = ''
as $$
begin
  perform 1
  from public.digitization_jobs
  where id = p_job_id
    and user_id = auth.uid()
    and status in ('draft', 'saved')
  for update;

  if not found then
    raise exception 'Deletable job not found or not owned by the current user';
  end if;
  if exists (
    select 1 from storage.objects o
    where o.bucket_id in ('pxrd-user-uploads', 'pxrd-user-results')
      and o.name like auth.uid()::text || '/' || p_job_id::text || '/%'
  ) then
    raise exception 'Remove private Storage objects before deleting the job record';
  end if;

  delete from public.digitization_jobs where id = p_job_id;
end;
$$;

drop function if exists public.submit_digitization_job(uuid);

revoke all on function public.create_digitization_job(integer, bigint, integer, integer, numeric, numeric, numeric) from public;
revoke all on function public.register_digitization_input(uuid, text, text, bigint) from public;
revoke all on function public.save_digitization_job(uuid) from public;
revoke all on function public.delete_digitization_job_record(uuid) from public;
grant execute on function public.create_digitization_job(integer, bigint, integer, integer, numeric, numeric, numeric) to authenticated;
grant execute on function public.register_digitization_input(uuid, text, text, bigint) to authenticated;
grant execute on function public.save_digitization_job(uuid) to authenticated;
grant execute on function public.delete_digitization_job_record(uuid) to authenticated;

commit;

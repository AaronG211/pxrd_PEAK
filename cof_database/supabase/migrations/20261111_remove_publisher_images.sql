-- ⚠️  SUPABASE SQL EDITOR: paste as is. This file has no `begin;` / `commit;`.
--     The editor runs the paste in its own transaction (README, "Running
--     migrations in the SQL editor"), and every statement here is idempotent,
--     so a run that failed can be pasted again. The script ENDS WITH A SELECT:
--     if it does not read image_columns_left = 0 and asset_mime_types =
--     {application/gzip}, the change did not land.
--
-- COF-PEAK migration: pxrd_figures carries no image path, and the public
-- bucket accepts no image.
--
-- Run once in the Supabase SQL editor, AFTER the frontend that no longer reads
-- these columns is live on main (DEPLOY_NOTES_2026-10-29.md, "2026-11-11"),
-- and BEFORE the next run of scripts/import_pxrd_to_supabase.py. Idempotent.
--
-- ---------------------------------------------------------------------------
-- WHY
-- ---------------------------------------------------------------------------
-- Until 2026-11-11 every paper page showed, for each figure, a crop of the
-- source article's figure (source.webp, path in crop_path) and the extracted
-- trace drawn over that crop (overlay.webp, path in overlay_path). Both are
-- made from the publisher's pixels. The authors decided that the site shows no
-- image taken from another author's figure, whatever its license. The page now
-- shows the figure's identity, its calibration checks, the interactive replot
-- and the CSV downloads, all drawn from our own curve data.
--
-- digitized_plot_path pointed at the pipeline's own QA replot
-- (digitized.webp). It holds no publisher pixels, but no page has shown or
-- selected it since the figure card lost its third tile, and the importer no
-- longer uploads it. A path that nothing reads goes with the other two.
--
-- WHAT IT DESTROYS. Three columns of object paths. The paths are rebuilt from
-- the figure ID (papers/<paper-id>/<figure-id>/<file>), so nothing is lost
-- that could not be written again. No paper, figure or curve row and no data
-- file changes.
-- curves.csv.gz stays in pxrd_curves.data_path.
--
-- WHAT IT DOES NOT DO. It deletes no Storage object: deleting rows from
-- storage.objects in SQL leaves the files behind. The publisher-derived
-- objects are removed by scripts/delete_publisher_images.py (dry run by
-- default, --apply to delete), which leaves curves.csv.gz and digitized.webp
-- alone.
--
-- BUCKET TYPES. pxrd-assets accepted any file type, so an older importer (the
-- one on main before this change, or a stale copy of cof_database/) would put
-- every source.webp back, uploading them before its first table write. From
-- here on Storage refuses any upload to pxrd-assets whose Content-Type is not
-- application/gzip. The objects already stored are not touched, and the
-- current importer uploads curves.csv.gz as application/gzip only. This stops
-- a mistake, not a deliberate upload of an image labelled as gzip.
-- pxrd-user-results, the private bucket of the hidden digitization workspace,
-- loses image/png and image/webp: no worker writes to it yet, and the types go
-- back only with a worker that stores our own renders
-- (digitization_workspace.sql carries the same list).
--
-- crop_path and digitized_plot_path are NOT NULL. The importer stopped sending
-- them, and Postgres checks NOT NULL on the proposed row before it resolves an
-- upsert's conflict, so every figure upsert would fail with 23502 until this
-- runs. The importer therefore checks for crop_path first and refuses a full
-- run, before any upload or upsert, while the column is there.
--
-- Nothing else depends on the three columns: pxrd_paper_index, the browse
-- snapshot, the sitemap and /api/v1 never read them (the API selects `*` but
-- returns named fields only). A dependency nobody knew of makes the ALTER fail
-- with "cannot drop column ... because other objects depend on it" and changes
-- nothing; there is deliberately no CASCADE.

update storage.buckets
   set allowed_mime_types = array['application/gzip']
 where id = 'pxrd-assets';

update storage.buckets
   set allowed_mime_types = array[
         'text/csv',
         'text/plain',
         'application/json',
         'application/zip',
         'application/octet-stream'
       ]
 where id = 'pxrd-user-results';

alter table public.pxrd_figures
  drop column if exists crop_path,
  drop column if exists digitized_plot_path,
  drop column if exists overlay_path;

-- PostgREST normally reloads its schema cache on DDL by itself. Asking costs
-- nothing and stops it advertising the dropped columns.
notify pgrst, 'reload schema';

-- Proof. Expected: image_columns_left 0, figures 4664, asset_mime_types
-- {application/gzip}, results_mime_types without image/png or image/webp
-- (null only if digitization_workspace.sql was never run).
select
  (select count(*)
     from information_schema.columns
    where table_schema = 'public'
      and table_name = 'pxrd_figures'
      and column_name in ('crop_path', 'digitized_plot_path', 'overlay_path')
  ) as image_columns_left,
  (select count(*) from public.pxrd_figures) as figures,
  (select allowed_mime_types from storage.buckets where id = 'pxrd-assets')
    as asset_mime_types,
  (select allowed_mime_types from storage.buckets where id = 'pxrd-user-results')
    as results_mime_types;

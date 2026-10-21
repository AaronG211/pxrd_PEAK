-- ⚠️  SUPABASE SQL EDITOR: strip the `begin;` / `commit;` in this file before
--     pasting. The editor wraps your script in its own transaction; a nested one
--     can fail partway, roll back everything, and still report "Success. No rows
--     returned". Every statement here is individually guarded, so running them
--     without an enclosing transaction is safe. See ../README.md, "Running
--     migrations in the SQL editor". The wrapper is correct for psql; keep it.
--
-- Open PXRD Database migration: publish every curve the paper releases.
--
-- Run once in the Supabase SQL editor, AFTER 20260910_curve_admission.sql and
-- BEFORE re-running scripts/import_pxrd_to_supabase.py. Idempotent; destroys no
-- data. Until the importer re-runs, every existing row is still 'clean' or
-- 'axis_verified', both of which the widened check accepts.
--
-- ---------------------------------------------------------------------------
-- WHY
-- ---------------------------------------------------------------------------
-- The paper releases 2,370 papers / 4,664 figures / 11,445 curves: every curve
-- the pipeline accepted, with its flags disclosed rather than the flagged
-- curves removed. Until this migration the site published 9,385 of them
-- (admission clean or axis_verified) and held the other 2,060 back. That
-- choice was never made deliberately, and part of it was inconsistent: 517
-- curves were withheld only because their figure's 2-theta axis was not
-- cross-validated, while 1,303 published clean curves sit on axes with the
-- same provenance.
--
-- From here the site publishes all 11,445, and each curve carries why it would
-- once have been held back instead of being held back:
--
--   clean             7,713  the curve and every sibling passed the shape checks
--   axis_verified     1,672  unflagged; a sibling was flagged; axis cross-validated
--   shape_flagged     1,339  this curve's own trace failed the offline shape checks
--   axis_unconfirmed    517  unflagged; a sibling was flagged; axis NOT
--                            cross-validated by two methods
--   model_derived       204  a label audit after extraction says this trace is
--                            simulated, refined or a difference curve that
--                            escaped the keyword filter
--
-- The mapping from outputs/pxrd.db (curves.admission / admission_reason) is
-- written once, in scripts/site_admission.py.
--
-- in_clean_set is NOT redefined. It stays the frozen 7,713-curve clean set
-- that the paper's validation section and outputs/analysis/*.json are computed
-- over, and pxrd_curves_admission_clean_set_check, in_clean_set =
-- (admission = 'clean'), is unchanged and keeps holding: only 'clean' maps to
-- 'clean'.
--
-- pxrd_paper_index needs no change. Nothing in it depends on there being two
-- classes: curve_count counts every published curve whatever its class, and
-- clean_curve_count / clean_figure_count filter on admission = 'clean', which
-- still means exactly the clean set. A paper whose clean_curve_count is 0 is
-- now one reachable only through any non-clean class (370 papers after the
-- re-import, up from 238), and the browse page's "clean curves only" option
-- keeps working on the same column. The view is left alone deliberately:
-- `create or replace view` can only append columns, and every column added to
-- it is permanent.
--
-- pxrd_figures.series_omitted_computed and series_excluded_quality count
-- curves the importer does NOT publish. After the re-import both are 0 on
-- every figure, because nothing is withheld; the columns stay so the counts
-- remain true if a future rule withholds a curve again.

begin;

-- Postgres has no "add constraint if not exists" for table constraints, so
-- drop-then-add is the idempotent form.
alter table public.pxrd_curves
  drop constraint if exists pxrd_curves_admission_check;
alter table public.pxrd_curves
  add constraint pxrd_curves_admission_check
  check (admission in (
    'clean',
    'axis_verified',
    'shape_flagged',
    'axis_unconfirmed',
    'model_derived'
  ));

-- 'excluded' is still deliberately NOT a legal value. Unpublished curves are
-- not rows in this table, and after this migration there are none.

commit;

-- If the importer later fails with `violates check constraint
-- "pxrd_curves_admission_check"`, this migration has not landed: the result
-- below came back with widened = false, or with no row at all.
--
-- After the importer re-runs, check the numbers rather than the status line:
--   select admission, count(*) from public.pxrd_curves group by 1 order by 1;
--     -> axis_unconfirmed 517, axis_verified 1672, clean 7713,
--        model_derived 204, shape_flagged 1339
--   select count(*) from public.pxrd_curves where in_clean_set;
--     -> 7713
--   select count(*), sum(curve_count), sum(clean_curve_count),
--          count(*) filter (where clean_curve_count = 0)
--   from public.pxrd_paper_index;
--     -> 2370, 11445, 7713, 370
--
-- PROOF (keep this last). The SQL editor shows the result of the final
-- statement, and reports "Success" even for a rolled-back script, so the file
-- ends by reading the constraint back from the catalogue. Expect one row with
-- widened = true. No row means the constraint is missing; widened = false
-- means the old two-value check is still installed.
select
  c.conname as constraint_name,
  pg_get_constraintdef(c.oid) as definition,
  (pg_get_constraintdef(c.oid) like '%shape_flagged%'
    and pg_get_constraintdef(c.oid) like '%axis_unconfirmed%'
    and pg_get_constraintdef(c.oid) like '%model_derived%') as widened
from pg_constraint c
where c.conrelid = 'public.pxrd_curves'::regclass
  and c.conname = 'pxrd_curves_admission_check';

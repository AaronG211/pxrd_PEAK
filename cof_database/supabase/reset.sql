-- DESTRUCTIVE: removes the legacy COF tables and any prior PXRD schema.
-- A local JSON backup must exist before running this file.

drop view if exists public.pxrd_paper_index cascade;
drop view if exists public.paper_index cascade;

drop table if exists public.pxrd_curves cascade;
drop table if exists public.pxrd_figures cascade;
drop table if exists public.pxrd_papers cascade;

drop table if exists public.gas_adsorption cascade;
drop table if exists public.evidence cascade;
drop table if exists public.cof_entities cascade;
drop table if exists public.papers cascade;

-- Storage objects/buckets are deleted through the Storage API, not direct SQL.
-- The reset workflow handles that separately before this script is run.

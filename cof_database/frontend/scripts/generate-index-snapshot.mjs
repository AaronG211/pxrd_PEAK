/**
 * Write public/data/paper-index.json before the Vite build: a snapshot of every
 * row the browse page needs, so it loads from one CDN file instead of Supabase.
 *
 * WHY. Measured from Chicago, the browse page made 34 Supabase requests before
 * its first row could render: six column probes, then 3 + 5 + 10 pages of the
 * paper index, figure facets and curve facets — each sweep strictly serial —
 * plus fourteen requests for the peak-search counts. Every request paid
 * 90-150 ms of transit between Cloudflare's edge and the database, and the
 * database itself only 15-150 ms. A first visit took 7.6 s to show a list; a
 * repeat visit 1.4-3.3 s. The data changes only when the importer runs.
 *
 * WHAT. Raw rows only — exactly what the live queries return, minus columns the
 * page never reads. Every derivation (facets, the material label index, the
 * peak-search counts) stays in src/lib/api.ts, applied identically to snapshot
 * rows and live rows, so there is one implementation of each and nothing here
 * can drift from it.
 *
 * FRESHNESS. The snapshot is as current as the last deploy. Paper pages still
 * read Supabase live, so a record is never stale; only the browse list can lag
 * an import that was not followed by a redeploy. The sitemap already has the
 * same property, and the importer prints a reminder.
 *
 * FAILURE IS SOFT, AS FOR THE SITEMAP. If Supabase is unreachable, or the rows
 * come back mutually inconsistent (a build that raced an import), no snapshot
 * is written and any old one is deleted. The page then falls back to the live
 * queries: slower, never wrong, never a failed deploy.
 */
import { existsSync, mkdirSync, readFileSync, renameSync, rmSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const outDir = resolve(here, "..", "public", "data");
const outFile = resolve(outDir, "paper-index.json");
const PAGE = 1000;
export const FORMAT = 1;

/**
 * Must match what src/lib/api.ts selects for the live paper index, and the
 * client checks these lists verbatim before trusting a snapshot.
 */
export const PAPER_COLUMNS = [
  "id", "paper_number", "doi", "title", "authors", "journal", "publication_year",
  "figure_count", "curve_count", "material_count",
  "publication_status", "publication_status_notice_doi",
  "clean_curve_count", "clean_figure_count",
];
/**
 * Every row is a tuple and every cross-reference is a position in `papers`.
 *
 * The page reads figure and curve rows only to attribute facets to a paper; no
 * figure or curve id is ever displayed. So a curve carries the index of its
 * paper rather than a 35-character figure id, and the 14 paper keys appear once
 * instead of 2,238 times. Measured on the 2,238-paper corpus: 410 KiB gzip /
 * 287 KiB brotli as objects carrying ids, 317 KiB gzip / 233 KiB brotli as
 * tuples. Most of what remains is titles and author lists, which search reads.
 */
export const FIGURE_COLUMNS = ["paper", "quality_status"];
export const CURVE_COLUMNS = [
  "paper", "material_name", "curve_role", "sample_state",
  "first_peak_status", "stacking_hump_status", "has_ratio",
];
const FIGURE_SELECT = "id,paper_id,quality_status";
const CURVE_SELECT = "figure_id,material_name,curve_role,sample_state,"
  + "first_peak_status,stacking_hump_status,intensity_ratio_100_001";

/**
 * The column groups whose existence gates the peak search, copied verbatim
 * from src/lib/api.ts (CURVE_FIRST_PEAK_COLUMNS, CURVE_DESCRIPTOR_COLUMNS) and
 * probed exactly as hasColumn() probes them: 42703 means absent, anything else
 * aborts the snapshot. A flag recorded true here makes the client select those
 * columns without asking, so it must never be true for a column that is not
 * there. Keep the two lists identical.
 */
const CAPABILITY_PROBES = {
  first_peak: ["pxrd_curves", "first_peak_two_theta_deg,first_peak_d_angstrom,first_peak_fwhm_deg,"
    + "first_peak_status,first_peak_wavelength_angstrom,first_peak_wavelength_source"],
  descriptors: ["pxrd_curves", "crystalline_fraction,stacking_hump_status,stacking_hump_center_deg,"
    + "stacking_hump_fwhm_deg,intensity_ratio_100_001,descriptor_version"],
  axis_uncertainty: ["pxrd_curves", "two_theta_uncertainty_deg"],
  verification_status: ["pxrd_figures", "verification_status"],
};

async function probe(url, key, table, columns) {
  const response = await fetch(`${url}/rest/v1/${table}?select=${columns}&limit=1`, {
    headers: { apikey: key, Authorization: `Bearer ${key}` },
    signal: AbortSignal.timeout(30_000),
  });
  if (response.ok) return true;
  const body = await response.json().catch(() => ({}));
  if (body.code === "42703") return false;
  throw new Error(`probe ${table}: PostgREST ${response.status} ${JSON.stringify(body)}`);
}

function readEnv() {
  const env = { ...process.env };
  for (const candidate of [resolve(here, "..", "..", ".env"), resolve(here, "..", ".env")]) {
    if (!existsSync(candidate)) continue;
    for (const line of readFileSync(candidate, "utf8").split("\n")) {
      const trimmed = line.trim();
      if (!trimmed || trimmed.startsWith("#") || !trimmed.includes("=")) continue;
      const index = trimmed.indexOf("=");
      const key = trimmed.slice(0, index).trim();
      const value = trimmed.slice(index + 1).trim().replace(/^["']|["']$/g, "");
      if (key && env[key] === undefined) env[key] = value;
    }
  }
  return env;
}

async function sweep(url, key, table, select, order) {
  const rows = [];
  for (let from = 0; ; from += PAGE) {
    const response = await fetch(
      `${url}/rest/v1/${table}?select=${select}&order=${order}&limit=${PAGE}&offset=${from}`,
      { headers: { apikey: key, Authorization: `Bearer ${key}` }, signal: AbortSignal.timeout(30_000) },
    );
    if (!response.ok) throw new Error(`${table}: PostgREST ${response.status} ${await response.text()}`);
    const page = await response.json();
    rows.push(...page);
    if (page.length < PAGE) return rows;
  }
}

function discard(reason) {
  console.warn(`[index-snapshot] ${reason}; no snapshot written, the browse page will query live.`);
  rmSync(outFile, { force: true });
}

const env = readEnv();
const url = env.VITE_SUPABASE_URL?.trim().replace(/\/$/, "");
const key = env.VITE_SUPABASE_ANON_KEY?.trim();

if (!url || !key) {
  discard("no Supabase credentials");
} else {
  try {
    const started = Date.now();
    const probed = await Promise.all(
      Object.entries(CAPABILITY_PROBES).map(async ([name, [table, columns]]) =>
        [name, await probe(url, key, table, columns)]),
    );
    const capabilities = Object.fromEntries(probed);
    const [papers, figures, curves] = await Promise.all([
      sweep(url, key, "pxrd_paper_index", PAPER_COLUMNS.join(","), "paper_number"),
      sweep(url, key, "pxrd_figures", FIGURE_SELECT, "id"),
      // Ordered by id for stable paging; id itself is never read by the page.
      sweep(url, key, "pxrd_curves", CURVE_SELECT, "id"),
    ]);

    // Three sweeps are three snapshots in time. If an import landed between
    // them they disagree, and a page built on that would show counts and
    // facets from different corpora. Check the joins before trusting them.
    const paperIds = new Set(papers.map((p) => p.id));
    const figurePaper = new Map(figures.map((f) => [f.id, f.paper_id]));
    const orphanFigures = figures.filter((f) => !paperIds.has(f.paper_id)).length;
    const orphanCurves = curves.filter((c) => !figurePaper.has(c.figure_id)).length;
    const indexCurves = papers.reduce((n, p) => n + (p.curve_count ?? 0), 0);
    const indexFigures = papers.reduce((n, p) => n + (p.figure_count ?? 0), 0);

    if (orphanFigures || orphanCurves || indexCurves !== curves.length || indexFigures !== figures.length) {
      discard(
        `rows disagree (orphan figures ${orphanFigures}, orphan curves ${orphanCurves}, ` +
        `index counts ${indexFigures}/${indexCurves} vs rows ${figures.length}/${curves.length})`,
      );
    } else {
      const paperIndex = new Map(papers.map((p, i) => [p.id, i]));
      const snapshot = {
        format: FORMAT,
        generated_at: new Date().toISOString(),
        capabilities,
        paper_columns: PAPER_COLUMNS,
        papers: papers.map((p) => PAPER_COLUMNS.map((c) => p[c] ?? null)),
        figure_columns: FIGURE_COLUMNS,
        figures: figures.map((f) => [paperIndex.get(f.paper_id), f.quality_status]),
        curve_columns: CURVE_COLUMNS,
        curves: curves.map((c) => [
          paperIndex.get(figurePaper.get(c.figure_id)),
          c.material_name ?? null,
          c.curve_role ?? null,
          c.sample_state ?? null,
          c.first_peak_status ?? null,
          c.stacking_hump_status ?? null,
          // Only whether a ratio exists is ever counted, never its value.
          c.intensity_ratio_100_001 === null || c.intensity_ratio_100_001 === undefined ? 0 : 1,
        ]),
      };
      mkdirSync(outDir, { recursive: true });
      // Write then rename, so a crash never leaves half a file to be deployed.
      const tmp = `${outFile}.tmp`;
      writeFileSync(tmp, JSON.stringify(snapshot));
      renameSync(tmp, outFile);
      const kib = Math.round(JSON.stringify(snapshot).length / 1024);
      console.log(
        `[index-snapshot] ${papers.length} papers, ${figures.length} figures, ${curves.length} curves ` +
        `-> public/data/paper-index.json (${kib} KiB before compression, ${Date.now() - started} ms)`,
      );
    }
  } catch (cause) {
    discard(`Supabase unreachable or erroring (${cause.message})`);
  }
}

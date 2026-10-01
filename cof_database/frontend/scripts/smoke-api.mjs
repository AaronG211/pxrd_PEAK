/**
 * Smoke test for api/v1.ts against the LIVE Supabase project.
 *
 *   npm run test:api
 *
 * Imports the handler directly and calls it with Web Requests, so it needs no
 * Vercel CLI and no deployment. Node's type stripping loads the .ts source.
 *
 * Assertions are about the API's own consistency, never about corpus size: a
 * hardcoded "2,238 papers" goes stale on the next import, but "the paper list's
 * total equals /stats.papers" stays true for as long as the API is correct.
 */
import { readFileSync, existsSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
for (const file of [resolve(here, "..", "..", ".env"), resolve(here, "..", ".env")]) {
  if (!existsSync(file)) continue;
  for (const line of readFileSync(file, "utf8").split("\n")) {
    const m = line.match(/^\s*([A-Z0-9_]+)\s*=\s*(.*)\s*$/);
    if (m && process.env[m[1]] === undefined) process.env[m[1]] = m[2].replace(/^["']|["']$/g, "");
  }
}
// The deployed function sees only the public key; so must the test.
delete process.env.SUPABASE_SECRET_KEY;

const api = (await import("../api/v1.ts")).default;

let passed = 0;
const failures = [];

/** Simulates the vercel.json rewrite: /api/v1/<path>?q  ->  /api/v1?path=<path>&q */
async function call(path, query = "", init = {}) {
  const sep = query ? `&${query}` : "";
  const url = path === "" ? `https://x/api/v1${query ? `?${query}` : ""}` : `https://x/api/v1?path=${path}${sep}`;
  const response = await api.fetch(new Request(url, init));
  const type = response.headers.get("content-type") ?? "";
  const body = type.includes("json") ? await response.json() : await response.text();
  return { status: response.status, headers: response.headers, body };
}

function check(name, condition, detail = "") {
  // A thrown TypeError from an unexpected response shape is a failure of that
  // check, not a reason to stop reporting the rest.
  if (typeof condition === "function") {
    try { condition = condition(); } catch (err) { condition = false; detail = `${detail} (${err.message})`; }
  }
  if (condition) passed++;
  else failures.push(`${name}${detail ? ` — ${detail}` : ""}`);
}

// --- index and stats -------------------------------------------------------
const idx = await call("");
check("index 200", idx.status === 200);
check("index lists endpoints", Array.isArray(idx.body.endpoints) && idx.body.endpoints.length >= 8);
check("CORS header", idx.headers.get("access-control-allow-origin") === "*");
check("cache header on 200", (idx.headers.get("cache-control") ?? "").includes("s-maxage"));

const stats = (await call("stats")).body;
check("stats has counts", stats.papers > 0 && stats.figures > 0 && stats.curves > 0, JSON.stringify(stats));
check("admission classes sum to total", stats.by_admission.clean + stats.by_admission.axis_verified === stats.curves);

// --- papers ----------------------------------------------------------------
const papers = await call("papers", "limit=5");
check("papers 200", papers.status === 200);
check("papers total == stats.papers", papers.body.meta.total === stats.papers, `${papers.body.meta.total} vs ${stats.papers}`);
check("papers honours limit", papers.body.data.length === 5);
check("papers next link", typeof papers.body.meta.next === "string" && papers.body.meta.next.includes("offset=5"));

const cleanOnly = await call("papers", "include_readmitted=false&limit=1");
check("include_readmitted=false total == papers_with_a_clean_curve",
  cleanOnly.body.meta.total === stats.papers_with_a_clean_curve,
  `${cleanOnly.body.meta.total} vs ${stats.papers_with_a_clean_curve}`);

const search = await call("papers", "q=triazine&limit=50");
check("search finds results", search.body.data.length > 0);
check("search results actually match", search.body.data.every((p) =>
  [p.title, p.doi, p.authors].some((v) => (v ?? "").toLowerCase().includes("triazine"))));

const hostile = await call("papers", `q=${encodeURIComponent('a,b),(title.eq."x')}`);
check("hostile search text is data, not syntax", hostile.status === 200, `status ${hostile.status}`);

const year = await call("papers", "year=2019&limit=200");
check("year filter", year.body.data.length > 0 && year.body.data.every((p) => p.year === 2019));

const typo = await call("papers", "limt=10");
check("unknown parameter rejected", typo.status === 400 && typo.body.error.code === "unknown_parameter");
check("unknown parameter lists allowed", Array.isArray(typo.body.error.allowed_parameters));
check("limit bound enforced", (await call("papers", "limit=5000")).status === 400);
check("error not cached", (typo.headers.get("cache-control") ?? "") === "no-store");

const pastEnd = await call("papers", `offset=${stats.papers + 10}`);
check("offset past end: empty, no next", () => pastEnd.status === 200 && pastEnd.body.data.length === 0 && pastEnd.body.meta.next === null && pastEnd.body.meta.total === stats.papers, `status ${pastEnd.status}`);

const somePaper = papers.body.data[0];
const detail = await call(`papers/${somePaper.id}`);
check("paper detail 200", detail.status === 200);
check("paper detail has figures with curves",
  detail.body.figures.length > 0 && detail.body.figures.every((f) => Array.isArray(f.curves)));
check("paper detail curve count matches index",
  detail.body.figures.reduce((n, f) => n + f.curves.length, 0) === somePaper.counts.curves);
check("missing paper 404", (await call("papers/10.0000_does.not.exist")).status === 404);
check("traversal-shaped id 404", (await call(`papers/${encodeURIComponent("../../etc")}`)).status === 404);

// --- curves ----------------------------------------------------------------
const window = await call("curves", "first_peak_min=3.0&first_peak_max=3.2&first_peak_status=ok&limit=1000");
check("curve window 200", window.status === 200);
check("curve window non-empty", window.body.data.length > 0);
check("curve window respected", window.body.data.every((c) =>
  c.first_peak && c.first_peak.two_theta_deg >= 3.0 && c.first_peak.two_theta_deg <= 3.2 && c.first_peak.status === "ok"));

const readmitted = await call("curves", "admission=axis_verified&limit=1");
check("admission filter total == stats", readmitted.body.meta.total === stats.by_admission.axis_verified);

const byPaper = await call("curves", `paper_id=${somePaper.id}&limit=1000`);
check("paper_id filter restricts", byPaper.body.data.length === somePaper.counts.curves
  && byPaper.body.data.every((c) => c.paper_id === somePaper.id),
  `${byPaper.body.data.length} vs ${somePaper.counts.curves}`);

const axis = await call("curves", "axis_verification=axis_arbitrated&limit=50");
check("axis filter restricts", axis.body.data.length > 0 && axis.body.data.every((c) => c.axis_verification === "axis_arbitrated"));

check("min > max rejected", (await call("curves", "first_peak_min=10&first_peak_max=5")).status === 400);
check("bad enum rejected", (await call("curves", "first_peak_status=great")).status === 400);

const honest = window.body.data[0];
check("public names do not claim Miller indices",
  "first_peak_to_hump_ratio" in honest.crystallinity && !("intensity_ratio_100_001" in honest.crystallinity));
check("d-spacing labelled derived", honest.first_peak && "d_spacing_angstrom_derived" in honest.first_peak);

const one = await call(`curves/${honest.series_id}`);
check("curve detail 200", one.status === 200);
check("peak list labelled unvetted", "unvetted_peaks" in one.body && !("peaks" in one.body));

const data = await call(`curves/${honest.series_id}/data`);
check("curve data 200", data.status === 200);
check("curve data arrays aligned", data.body.two_theta_deg.length === data.body.relative_intensity.length);
check("curve data length == point_count", data.body.point_count === honest.point_count,
  `${data.body.point_count} vs ${honest.point_count}`);
check("curve data ascending 2θ", data.body.two_theta_deg.every((v, i, a) => i === 0 || v >= a[i - 1]));
check("curve data within stated range",
  Math.abs(data.body.two_theta_deg[0] - honest.two_theta_min_deg) < 0.01
  && Math.abs(data.body.two_theta_deg.at(-1) - honest.two_theta_max_deg) < 0.01);

const csv = await call(`curves/${honest.series_id}/data`, "format=csv");
check("curve csv", csv.status === 200 && csv.body.startsWith("two_theta_deg,relative_intensity\n"));
check("curve csv rows", csv.body.trim().split("\n").length === honest.point_count + 1);

// A label or material name containing a comma is the case that breaks naive CSV
// splitting. Find one and make sure its points still parse.
const commaCurve = (await call("curves", "material=,&limit=1")).body.data[0];
if (commaCurve) {
  const d = await call(`curves/${commaCurve.series_id}/data`);
  check("comma in material name parses", d.status === 200 && d.body.point_count === commaCurve.point_count
    && d.body.relative_intensity.every(Number.isFinite), commaCurve.series_id);
} else {
  failures.push("no curve with a comma in its material name to exercise the CSV parser");
}

// --- figures ---------------------------------------------------------------
const fig = await call(`figures/${honest.figure_id}`);
check("figure detail 200", fig.status === 200 && Array.isArray(fig.body.curves));
check("figure contains the curve", fig.body.curves.some((c) => c.series_id === honest.series_id));
const figData = await call(`figures/${honest.figure_id}/data`);
check("figure data redirects to storage", figData.status === 302
  && (figData.headers.get("location") ?? "").endsWith("/curves.csv.gz"));

// --- protocol --------------------------------------------------------------
check("unknown route 404", (await call("nonsense")).status === 404);
check("too-deep route 404", (await call("curves/a/b/c")).status === 404);
check("POST rejected", (await call("stats", "", { method: "POST" })).status === 405);
check("OPTIONS preflight", (await call("stats", "", { method: "OPTIONS" })).status === 204);
check("malformed encoding 400", (await call("papers/%E0%A4%A")).status === 400);
// Without the rewrite: the function must also understand its own pathname.
const direct = await api.fetch(new Request("https://x/api/v1/stats"));
check("pathname routing works too", direct.status === 200);

console.log(`\n${passed} passed, ${failures.length} failed`);
for (const f of failures) console.log(`  ✗ ${f}`);
process.exit(failures.length ? 1 : 0);

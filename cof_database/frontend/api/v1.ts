/**
 * Open PXRD Database — public read-only API, version 1.
 *
 *   https://pxrd-peak.vercel.app/api/v1
 *
 * WHY THIS EXISTS RATHER THAN DOCUMENTING SUPABASE. The site's data already sits
 * behind Supabase's auto-generated REST API, readable with the anon key that
 * ships in the frontend bundle. Pointing users at it would make the internal
 * schema the public contract: every column rename would break someone, and two
 * internal names are actively misleading (`intensity_ratio_100_001` asserts a
 * Miller-index assignment nobody made; `crystalline_fraction` is not a degree of
 * crystallinity). This file is the contract instead. Internal names can keep
 * moving; what is published here changes only with a new version prefix.
 *
 * WHY ONE FILE. Vercel compiles functions in /api without honouring TypeScript
 * project references or path mappings, and Node's type stripping (used by the
 * local smoke test) needs `.ts` import specifiers where the deployed ESM needs
 * `.js`. A single module with no relative imports sidesteps both. vercel.json
 * rewrites /api/v1/<anything> here with the remainder in `?path=`.
 *
 * SECURITY. Only the anon key is used, never the service key, so row-level
 * security bounds what this can ever return to what is already public — a bug
 * here can mis-shape public data but cannot leak private data. Every path
 * segment is matched against the corpus's actual ID alphabet before it reaches
 * a query, and free text is quoted per PostgREST's grammar.
 *
 * LOAD. Responses carry s-maxage, so Vercel's CDN serves repeats without
 * invoking the function or touching Supabase. The data changes only when the
 * importer runs.
 */

import { gunzipSync } from "node:zlib";

const VERSION = "1";
const SITE = "https://pxrd-peak.vercel.app";
const BUCKET = "pxrd-assets";

/** Every ID in the corpus matches this; anything else cannot exist. */
const ID_PATTERN = /^[A-Za-z0-9._()-]{1,80}$/;

const CURVE_ROLES = ["experimental", "simulated", "refined", "difference", "reference", "unclassified"];
const ADMISSIONS = ["clean", "axis_verified"];
const FIRST_PEAK_STATUSES = ["ok", "low_confidence", "truncated_at_window_start", "no_bragg_peak"];
const HUMP_STATUSES = ["hump_detected", "no_hump_detected", "window_not_covered", "not_computed"];
const AXIS_STATUSES = ["axis_cross_validated", "axis_single_method", "axis_arbitrated", "axis_unverified"];
const PUBLICATION_STATUSES = ["active", "corrected", "retracted", "withdrawn", "concern", "unchecked"];

const CACHE_OK = "public, max-age=300, s-maxage=3600, stale-while-revalidate=86400";

// ---------------------------------------------------------------------------
// Responses
// ---------------------------------------------------------------------------

class ApiError extends Error {
  status: number;
  code: string;
  detail: Record<string, unknown> | undefined;
  constructor(status: number, code: string, message: string, detail?: Record<string, unknown>) {
    super(message);
    this.status = status;
    this.code = code;
    this.detail = detail;
  }
}

function baseHeaders(): Record<string, string> {
  return {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, HEAD, OPTIONS",
    "X-Api-Version": VERSION,
  };
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body, null, 2), {
    status,
    headers: {
      ...baseHeaders(),
      "Content-Type": "application/json; charset=utf-8",
      // Errors are not cached: a 502 from a transient upstream failure must
      // not be pinned at the edge for an hour.
      "Cache-Control": status === 200 ? CACHE_OK : "no-store",
    },
  });
}

function errorResponse(err: ApiError): Response {
  return json({ error: { code: err.code, message: err.message, ...(err.detail ?? {}) } }, err.status);
}

// ---------------------------------------------------------------------------
// Parameters
// ---------------------------------------------------------------------------

/**
 * Reads query parameters and rejects any it was not told about. A typo such as
 * `limt=10` silently returning the default page is the kind of bug that ends up
 * in someone's dataset, so unknown names are a 400 that lists the valid ones.
 */
class Params {
  private readonly source: URLSearchParams;
  private readonly used = new Set<string>(["path"]);

  constructor(source: URLSearchParams) {
    this.source = source;
  }

  str(name: string, maxLength = 100): string | null {
    this.used.add(name);
    const value = this.source.get(name);
    if (value === null || value.trim() === "") return null;
    if (value.length > maxLength) {
      throw new ApiError(400, "invalid_parameter", `'${name}' is longer than ${maxLength} characters.`);
    }
    return value.trim();
  }

  num(name: string, min: number, max: number): number | null {
    const raw = this.str(name, 32);
    if (raw === null) return null;
    const value = Number(raw);
    if (!Number.isFinite(value) || value < min || value > max) {
      throw new ApiError(400, "invalid_parameter", `'${name}' must be a number between ${min} and ${max}.`);
    }
    return value;
  }

  int(name: string, min: number, max: number, fallback: number): number {
    const value = this.num(name, min, max);
    if (value === null) return fallback;
    if (!Number.isInteger(value)) {
      throw new ApiError(400, "invalid_parameter", `'${name}' must be an integer.`);
    }
    return value;
  }

  bool(name: string, fallback: boolean): boolean {
    const raw = this.str(name, 8);
    if (raw === null) return fallback;
    if (["true", "1", "yes"].includes(raw.toLowerCase())) return true;
    if (["false", "0", "no"].includes(raw.toLowerCase())) return false;
    throw new ApiError(400, "invalid_parameter", `'${name}' must be true or false.`);
  }

  oneOf(name: string, allowed: string[]): string | null {
    const raw = this.str(name, 64);
    if (raw === null) return null;
    if (!allowed.includes(raw)) {
      throw new ApiError(400, "invalid_parameter", `'${name}' must be one of: ${allowed.join(", ")}.`);
    }
    return raw;
  }

  /** Comma-separated subset of `allowed`. */
  subset(name: string, allowed: string[]): string[] | null {
    const raw = this.str(name, 200);
    if (raw === null) return null;
    const values = raw.split(",").map((v) => v.trim()).filter(Boolean);
    const bad = values.filter((v) => !allowed.includes(v));
    if (bad.length > 0 || values.length === 0) {
      throw new ApiError(400, "invalid_parameter", `'${name}' accepts a comma-separated subset of: ${allowed.join(", ")}.`);
    }
    return values;
  }

  id(name: string): string | null {
    const raw = this.str(name, 80);
    if (raw === null) return null;
    if (!ID_PATTERN.test(raw)) throw new ApiError(400, "invalid_parameter", `'${name}' is not a valid ID.`);
    return raw;
  }

  /** Call after reading every parameter the route accepts. */
  rejectUnknown(): void {
    const unknown = [...new Set(this.source.keys())].filter((key) => !this.used.has(key));
    if (unknown.length > 0) {
      const allowed = [...this.used].filter((key) => key !== "path").sort();
      throw new ApiError(400, "unknown_parameter", `Unknown parameter${unknown.length > 1 ? "s" : ""}: ${unknown.join(", ")}.`, {
        allowed_parameters: allowed,
      });
    }
  }
}

function pathId(segment: string | undefined, what: string): string {
  if (!segment || !ID_PATTERN.test(segment)) {
    throw new ApiError(404, "not_found", `No ${what} with that ID.`);
  }
  return segment;
}

/** PostgREST's quoted-value form, so commas and parentheses stay literal. */
function quoted(value: string): string {
  return `"${value.replace(/\\/g, "\\\\").replace(/"/g, '\\"')}"`;
}

/** Text for an ilike pattern: wildcards are ours to place, not the caller's. */
function likeText(value: string): string {
  return value.replace(/[*%]/g, " ").trim();
}

// ---------------------------------------------------------------------------
// Upstream
// ---------------------------------------------------------------------------

function config(): { url: string; key: string } {
  const url = (process.env.SUPABASE_URL ?? process.env.VITE_SUPABASE_URL ?? "").replace(/\/$/, "");
  // The anon key on purpose. See the SECURITY note at the top of the file.
  const key = process.env.SUPABASE_ANON_KEY ?? process.env.VITE_SUPABASE_ANON_KEY ?? "";
  if (!url || !key) throw new ApiError(500, "misconfigured", "The API is not configured on this deployment.");
  return { url, key };
}

type Query = { rows: Record<string, unknown>[]; total: number | null };

async function select(table: string, params: URLSearchParams, wantCount = false): Promise<Query> {
  const { url, key } = config();
  const headers: Record<string, string> = { apikey: key, Authorization: `Bearer ${key}` };
  if (wantCount) headers.Prefer = "count=exact";
  let response: Response;
  try {
    response = await fetch(`${url}/rest/v1/${table}?${params}`, {
      headers,
      signal: AbortSignal.timeout(10_000),
    });
  } catch (cause) {
    console.error("upstream fetch failed", table, cause);
    throw new ApiError(502, "upstream_unavailable", "The database did not respond. Try again shortly.");
  }
  // An offset past the last row is a 416 from PostgREST, but to a caller
  // paginating to the end it is simply an empty page. Content-Range still
  // carries the total ("*/2238"), so the page metadata stays correct.
  if (response.status === 416) {
    const range = response.headers.get("content-range") ?? "";
    const total = range.includes("/") ? Number(range.split("/")[1]) : null;
    return { rows: [], total: Number.isFinite(total) ? total : null };
  }
  if (!response.ok) {
    console.error("upstream error", table, response.status, await response.text());
    throw new ApiError(502, "upstream_error", "The database returned an error.");
  }
  const rows = (await response.json()) as Record<string, unknown>[];
  const range = response.headers.get("content-range");
  const total = range && range.includes("/") ? Number(range.split("/")[1]) : null;
  return { rows, total: Number.isFinite(total) ? total : null };
}

async function count(table: string, filters: Record<string, string> = {}): Promise<number> {
  const params = new URLSearchParams({ select: "id", limit: "1", ...filters });
  const { total } = await select(table, params, true);
  return total ?? 0;
}

function storageUrl(path: string): string {
  return `${config().url}/storage/v1/object/public/${BUCKET}/${path}`;
}

// ---------------------------------------------------------------------------
// Shaping: internal rows -> the published contract
// ---------------------------------------------------------------------------

const num = (value: unknown): number | null =>
  typeof value === "number" && Number.isFinite(value) ? value : null;
const text = (value: unknown): string | null =>
  typeof value === "string" && value !== "" ? value : null;

function paperLinks(id: string) {
  return {
    self: `${SITE}/api/v1/papers/${id}`,
    curves: `${SITE}/api/v1/curves?paper_id=${id}`,
    page: `${SITE}/paper/${id}`,
  };
}

function shapePaperSummary(row: Record<string, unknown>) {
  const id = String(row.id);
  return {
    id,
    paper_number: row.paper_number,
    doi: text(row.doi),
    title: text(row.title),
    authors: text(row.authors),
    journal: text(row.journal),
    year: num(row.publication_year),
    publication_status: text(row.publication_status),
    publication_status_notice_doi: text(row.publication_status_notice_doi),
    counts: {
      figures: num(row.figure_count),
      curves: num(row.curve_count),
      clean_curves: num(row.clean_curve_count),
      material_names: num(row.material_count),
    },
    links: paperLinks(id),
  };
}

const CURVE_COLUMNS = [
  "series_id", "figure_id", "label", "material_name", "sample_state", "curve_role",
  "admission", "two_theta_min", "two_theta_max", "point_count",
  "two_theta_uncertainty_deg", "trace_confidence",
  "first_peak_two_theta_deg", "first_peak_d_angstrom", "first_peak_fwhm_deg",
  "first_peak_status", "first_peak_wavelength_angstrom", "first_peak_wavelength_source",
  "crystalline_fraction", "stacking_hump_status", "stacking_hump_center_deg",
  "stacking_hump_fwhm_deg", "intensity_ratio_100_001", "descriptor_version",
].join(",");

function shapeCurve(row: Record<string, unknown>, figure?: Record<string, unknown>) {
  const seriesId = String(row.series_id);
  const figureId = String(row.figure_id);
  const paperId = text(figure?.paper_id) ?? figureId.replace(/-p\d+-f\d+$/, "");
  const firstPeak = num(row.first_peak_two_theta_deg) === null
    ? null
    : {
        two_theta_deg: num(row.first_peak_two_theta_deg),
        // Derived, never measured: lambda / (2 sin theta) at the wavelength
        // beside it. Named so a reader cannot mistake it for an observation.
        d_spacing_angstrom_derived: num(row.first_peak_d_angstrom),
        fwhm_deg: num(row.first_peak_fwhm_deg),
        status: text(row.first_peak_status),
        wavelength_angstrom: num(row.first_peak_wavelength_angstrom),
        wavelength_source: text(row.first_peak_wavelength_source),
      };
  return {
    series_id: seriesId,
    paper_id: paperId,
    figure_id: figureId,
    label: text(row.label),
    material_name: text(row.material_name),
    sample_state: text(row.sample_state),
    role: text(row.curve_role),
    admission: text(row.admission) ?? "clean",
    two_theta_min_deg: num(row.two_theta_min),
    two_theta_max_deg: num(row.two_theta_max),
    point_count: num(row.point_count),
    two_theta_uncertainty_deg: num(row.two_theta_uncertainty_deg),
    trace_confidence: num(row.trace_confidence),
    axis_verification: text(figure?.verification_status),
    first_peak: firstPeak,
    crystallinity: {
      // Fraction of intensity in features narrower than ~4 deg. NOT a degree
      // of crystallinity, and confounded by the plotted 2-theta window.
      sharp_feature_fraction: num(row.crystalline_fraction),
      stacking_hump_status: text(row.stacking_hump_status),
      stacking_hump_center_deg: num(row.stacking_hump_center_deg),
      stacking_hump_fwhm_deg: num(row.stacking_hump_fwhm_deg),
      // First-peak height over hump height, both within this one curve.
      // Stored internally as intensity_ratio_100_001; no Miller index was
      // ever assigned, so the public name does not claim one.
      first_peak_to_hump_ratio: num(row.intensity_ratio_100_001),
      descriptor_version: text(row.descriptor_version),
    },
    links: {
      self: `${SITE}/api/v1/curves/${seriesId}`,
      data: `${SITE}/api/v1/curves/${seriesId}/data`,
      figure: `${SITE}/api/v1/figures/${figureId}`,
      page: `${SITE}/paper/${paperId}#${figureId}`,
    },
  };
}

function shapeFigure(row: Record<string, unknown>) {
  const id = String(row.id);
  return {
    id,
    paper_id: row.paper_id,
    label: text(row.figure_label),
    page_number: num(row.page_number),
    caption: text(row.caption),
    flagged: row.quality_status === "flagged",
    axis_verification: text(row.verification_status),
    axis_agreement_deg: num(row.axis_agreement_deg),
    series: {
      detected: num(row.series_detected),
      digitized: num(row.series_digitized),
      computed_not_published: num(row.series_omitted_computed),
      withheld_by_quality_checks: num(row.series_excluded_quality),
    },
    links: {
      self: `${SITE}/api/v1/figures/${id}`,
      data: `${SITE}/api/v1/figures/${id}/data`,
      page: `${SITE}/paper/${row.paper_id}#${id}`,
    },
  };
}

function page(url: URL, limit: number, offset: number, returned: number, total: number | null) {
  let next: string | null = null;
  if (total !== null && offset + returned < total) {
    const nextUrl = new URL(url.toString());
    nextUrl.searchParams.delete("path");
    nextUrl.searchParams.set("offset", String(offset + limit));
    next = `${SITE}${publicPath(url)}${nextUrl.search}`;
  }
  return { total, limit, offset, returned, next };
}

function publicPath(url: URL): string {
  const path = url.searchParams.get("path");
  return path !== null ? `/api/v1/${path}`.replace(/\/$/, "") : url.pathname;
}

// ---------------------------------------------------------------------------
// Routes
// ---------------------------------------------------------------------------

function index() {
  return {
    name: "Open PXRD Database API",
    version: VERSION,
    description:
      "Read-only access to powder X-ray diffraction patterns digitized from published figures. "
      + "No key required. Responses are cached for up to an hour.",
    documentation: `${SITE}/docs/api`,
    endpoints: [
      { path: "/api/v1/stats", description: "Corpus counts." },
      { path: "/api/v1/papers", description: "List and search papers.", example: `${SITE}/api/v1/papers?q=triazine&limit=5` },
      { path: "/api/v1/papers/{paper_id}", description: "One paper with its figures and curves." },
      { path: "/api/v1/curves", description: "List and filter curves.", example: `${SITE}/api/v1/curves?first_peak_min=3.0&first_peak_max=3.2&first_peak_status=ok` },
      { path: "/api/v1/curves/{series_id}", description: "One curve's metadata, including its unvetted peak list." },
      { path: "/api/v1/curves/{series_id}/data", description: "One curve's 2-theta / intensity points. ?format=json (default) or csv." },
      { path: "/api/v1/figures/{figure_id}", description: "One figure with its curves." },
      { path: "/api/v1/figures/{figure_id}/data", description: "Every curve in one figure, as gzipped CSV (redirect)." },
    ],
    notes: [
      "Curves are digitized from published images, not raw diffractometer files.",
      "Intensities are relative and per-curve; they are not comparable across curves.",
      "d-spacings are derived from 2-theta at an assumed Cu K-alpha wavelength unless wavelength_source says otherwise.",
      "When training models, split by paper_id: curves in one figure share a material and an axis.",
    ],
  };
}

async function stats() {
  const [papers, cleanPapers, figures, curves, cleanCurves] = await Promise.all([
    count("pxrd_papers"),
    count("pxrd_paper_index", { clean_curve_count: "gt.0" }),
    count("pxrd_figures"),
    count("pxrd_curves"),
    count("pxrd_curves", { admission: "eq.clean" }),
  ]);
  return {
    papers,
    figures,
    curves,
    by_admission: { clean: cleanCurves, axis_verified: curves - cleanCurves },
    papers_with_a_clean_curve: cleanPapers,
  };
}

async function listPapers(url: URL, params: Params) {
  const q = params.str("q");
  const year = params.int("year", 1800, 2200, 0) || null;
  const yearMin = params.num("year_min", 1800, 2200);
  const yearMax = params.num("year_max", 1800, 2200);
  const journal = params.str("journal");
  const status = params.oneOf("publication_status", PUBLICATION_STATUSES);
  const includeReadmitted = params.bool("include_readmitted", true);
  const sort = params.oneOf("sort", ["paper_number", "year", "-year", "-curves"]) ?? "paper_number";
  const limit = params.int("limit", 1, 1000, 100);
  const offset = params.int("offset", 0, 1_000_000, 0);
  params.rejectUnknown();

  const query = new URLSearchParams({
    select: "id,paper_number,doi,title,authors,journal,publication_year,publication_status,"
      + "publication_status_notice_doi,figure_count,curve_count,clean_curve_count,material_count",
    limit: String(limit),
    offset: String(offset),
    order: {
      paper_number: "paper_number.asc",
      year: "publication_year.asc.nullslast,paper_number.asc",
      "-year": "publication_year.desc.nullslast,paper_number.asc",
      "-curves": "curve_count.desc,paper_number.asc",
    }[sort] as string,
  });
  if (q) {
    const pattern = quoted(`*${likeText(q)}*`);
    query.set("or", `(title.ilike.${pattern},doi.ilike.${pattern},authors.ilike.${pattern})`);
  }
  if (year !== null) query.append("publication_year", `eq.${year}`);
  if (yearMin !== null) query.append("publication_year", `gte.${yearMin}`);
  if (yearMax !== null) query.append("publication_year", `lte.${yearMax}`);
  if (journal) query.set("journal", `ilike.*${likeText(journal)}*`);
  if (status) query.set("publication_status", `eq.${status}`);
  if (!includeReadmitted) query.set("clean_curve_count", "gt.0");

  const { rows, total } = await select("pxrd_paper_index", query, true);
  return { data: rows.map(shapePaperSummary), meta: page(url, limit, offset, rows.length, total) };
}

async function getPaper(id: string) {
  const query = new URLSearchParams({
    id: `eq.${id}`,
    select: `*,pxrd_figures(*,pxrd_curves(${CURVE_COLUMNS}))`,
  });
  const { rows } = await select("pxrd_papers", query);
  const row = rows[0];
  if (!row) throw new ApiError(404, "not_found", "No paper with that ID.");
  const figures = ((row.pxrd_figures as Record<string, unknown>[]) ?? [])
    .sort((a, b) => Number(a.sort_order) - Number(b.sort_order));
  return {
    id,
    paper_number: row.paper_number,
    doi: text(row.doi),
    title: text(row.title),
    authors: text(row.authors),
    journal: text(row.journal),
    year: num(row.publication_year),
    publication_status: text(row.publication_status),
    publication_status_notice_doi: text(row.publication_status_notice_doi),
    figures: figures.map((figure) => ({
      ...shapeFigure(figure),
      curves: ((figure.pxrd_curves as Record<string, unknown>[]) ?? [])
        .sort((a, b) => String(a.series_id).localeCompare(String(b.series_id)))
        .map((curve) => shapeCurve(curve, figure)),
    })),
    links: paperLinks(id),
  };
}

async function listCurves(url: URL, params: Params) {
  const paperId = params.id("paper_id");
  const figureId = params.id("figure_id");
  const material = params.str("material");
  const role = params.oneOf("role", CURVE_ROLES);
  const admission = params.oneOf("admission", ADMISSIONS);
  const peakMin = params.num("first_peak_min", 0, 180);
  const peakMax = params.num("first_peak_max", 0, 180);
  const peakStatus = params.subset("first_peak_status", FIRST_PEAK_STATUSES);
  const hump = params.oneOf("stacking_hump_status", HUMP_STATUSES);
  const axis = params.oneOf("axis_verification", AXIS_STATUSES);
  const sort = params.oneOf("sort", ["series_id", "first_peak", "-first_peak"]) ?? "series_id";
  const limit = params.int("limit", 1, 1000, 100);
  const offset = params.int("offset", 0, 1_000_000, 0);
  params.rejectUnknown();

  if (peakMin !== null && peakMax !== null && peakMin > peakMax) {
    throw new ApiError(400, "invalid_parameter", "'first_peak_min' is greater than 'first_peak_max'.");
  }

  // !inner makes the figure filters below restrict the curves, not just the
  // embedded object. paper_id lives on the figure, not the curve.
  const query = new URLSearchParams({
    select: `${CURVE_COLUMNS},pxrd_figures!inner(paper_id,verification_status)`,
    limit: String(limit),
    offset: String(offset),
    order: {
      series_id: "series_id.asc",
      first_peak: "first_peak_two_theta_deg.asc.nullslast,series_id.asc",
      "-first_peak": "first_peak_two_theta_deg.desc.nullslast,series_id.asc",
    }[sort] as string,
  });
  if (paperId) query.set("pxrd_figures.paper_id", `eq.${paperId}`);
  if (figureId) query.set("figure_id", `eq.${figureId}`);
  if (material) query.set("material_name", `ilike.*${likeText(material)}*`);
  if (role) query.set("curve_role", `eq.${role}`);
  if (admission) query.set("admission", `eq.${admission}`);
  if (peakMin !== null) query.append("first_peak_two_theta_deg", `gte.${peakMin}`);
  if (peakMax !== null) query.append("first_peak_two_theta_deg", `lte.${peakMax}`);
  if (peakStatus) query.set("first_peak_status", `in.(${peakStatus.join(",")})`);
  if (hump) query.set("stacking_hump_status", `eq.${hump}`);
  if (axis) query.set("pxrd_figures.verification_status", `eq.${axis}`);

  const { rows, total } = await select("pxrd_curves", query, true);
  return {
    data: rows.map((row) => shapeCurve(row, row.pxrd_figures as Record<string, unknown>)),
    meta: page(url, limit, offset, rows.length, total),
  };
}

async function curveRow(seriesId: string, extra = "") {
  const query = new URLSearchParams({
    series_id: `eq.${seriesId}`,
    select: `${CURVE_COLUMNS},data_path${extra},pxrd_figures(paper_id,verification_status)`,
  });
  const { rows } = await select("pxrd_curves", query);
  const row = rows[0];
  if (!row) throw new ApiError(404, "not_found", "No curve with that ID.");
  return row;
}

async function getCurve(seriesId: string) {
  const row = await curveRow(seriesId, ",peaks,peak_list_truncated");
  return {
    ...shapeCurve(row, row.pxrd_figures as Record<string, unknown>),
    // The automatic peak list passes none of the physics gates behind
    // first_peak.status, so the name says so.
    unvetted_peaks: Array.isArray(row.peaks) ? row.peaks : null,
    unvetted_peaks_truncated: row.peak_list_truncated === true,
  };
}

/**
 * Minimal RFC 4180 reader. Material names and labels can carry commas and
 * quotes, so splitting on "," would silently shift columns.
 */
function parseCsv(textBody: string): string[][] {
  const rows: string[][] = [];
  let row: string[] = [];
  let field = "";
  let quotedField = false;
  for (let i = 0; i < textBody.length; i++) {
    const ch = textBody[i];
    if (quotedField) {
      if (ch === '"') {
        if (textBody[i + 1] === '"') { field += '"'; i++; } else { quotedField = false; }
      } else {
        field += ch;
      }
    } else if (ch === '"') {
      quotedField = true;
    } else if (ch === ",") {
      row.push(field); field = "";
    } else if (ch === "\n" || ch === "\r") {
      if (ch === "\r" && textBody[i + 1] === "\n") i++;
      row.push(field); field = "";
      rows.push(row); row = [];
    } else {
      field += ch;
    }
  }
  if (field !== "" || row.length > 0) { row.push(field); rows.push(row); }
  return rows;
}

async function curveData(url: URL, seriesId: string, params: Params) {
  const format = params.oneOf("format", ["json", "csv"]) ?? "json";
  params.rejectUnknown();
  const row = await curveRow(seriesId);
  const dataPath = text(row.data_path);
  if (!dataPath) throw new ApiError(404, "not_found", "This curve has no published data file.");

  let response: Response;
  try {
    response = await fetch(storageUrl(dataPath), { signal: AbortSignal.timeout(15_000) });
  } catch (cause) {
    console.error("storage fetch failed", dataPath, cause);
    throw new ApiError(502, "upstream_unavailable", "The data store did not respond. Try again shortly.");
  }
  if (!response.ok) throw new ApiError(502, "upstream_error", "The data file could not be read.");

  const rows = parseCsv(gunzipSync(Buffer.from(await response.arrayBuffer())).toString("utf8"));
  const header = rows[0] ?? [];
  const at = (name: string) => header.indexOf(name);
  const [iSeries, iX, iY] = [at("series_id"), at("two_theta_deg"), at("relative_intensity")];
  if (iSeries < 0 || iX < 0 || iY < 0) throw new ApiError(502, "upstream_error", "The data file has an unexpected layout.");

  const x: number[] = [];
  const y: number[] = [];
  for (const r of rows.slice(1)) {
    if (r[iSeries] !== seriesId) continue;
    x.push(Number(r[iX]));
    y.push(Number(r[iY]));
  }
  if (x.length === 0) throw new ApiError(404, "not_found", "No points were published for this curve.");

  if (format === "csv") {
    const body = ["two_theta_deg,relative_intensity", ...x.map((v, i) => `${v},${y[i]}`)].join("\n") + "\n";
    return new Response(body, {
      status: 200,
      headers: {
        ...baseHeaders(),
        "Content-Type": "text/csv; charset=utf-8",
        "Content-Disposition": `attachment; filename="${seriesId}.csv"`,
        "Cache-Control": CACHE_OK,
      },
    });
  }
  const shaped = shapeCurve(row, row.pxrd_figures as Record<string, unknown>);
  return json({
    series_id: seriesId,
    point_count: x.length,
    two_theta_deg: x,
    relative_intensity: y,
    two_theta_uncertainty_deg: shaped.two_theta_uncertainty_deg,
    units: {
      two_theta_deg: "degrees 2-theta, from the figure's calibrated axis",
      relative_intensity: "arbitrary; scaled per curve so the tallest feature is roughly 100; not comparable across curves",
    },
    links: { ...shaped.links, csv: `${SITE}${publicPath(url)}?format=csv` },
  });
}

async function getFigure(figureId: string) {
  const query = new URLSearchParams({
    id: `eq.${figureId}`,
    select: `*,pxrd_curves(${CURVE_COLUMNS})`,
  });
  const { rows } = await select("pxrd_figures", query);
  const row = rows[0];
  if (!row) throw new ApiError(404, "not_found", "No figure with that ID.");
  return {
    ...shapeFigure(row),
    curves: ((row.pxrd_curves as Record<string, unknown>[]) ?? [])
      .sort((a, b) => String(a.series_id).localeCompare(String(b.series_id)))
      .map((curve) => shapeCurve(curve, row)),
  };
}

async function figureData(figureId: string) {
  const query = new URLSearchParams({ figure_id: `eq.${figureId}`, select: "data_path", limit: "1" });
  const { rows } = await select("pxrd_curves", query);
  const dataPath = text(rows[0]?.data_path);
  if (!dataPath) throw new ApiError(404, "not_found", "No figure with that ID.");
  // A redirect, not a proxy: the file is public already, and streaming it
  // through the function would spend function time for nothing.
  return new Response(null, {
    status: 302,
    headers: { ...baseHeaders(), Location: storageUrl(dataPath), "Cache-Control": CACHE_OK },
  });
}

async function route(request: Request): Promise<Response> {
  const url = new URL(request.url);
  const raw = url.searchParams.get("path")
    ?? url.pathname.replace(/^\/api\/v1\/?/, "");
  let segments: string[];
  try {
    segments = raw.split("/").filter(Boolean).map((s) => decodeURIComponent(s));
  } catch {
    throw new ApiError(400, "invalid_path", "The request path is not valid URL encoding.");
  }
  const params = new Params(url.searchParams);
  const [resource, id, sub] = segments;

  if (segments.length === 0) { params.rejectUnknown(); return json(index()); }
  if (segments.length > 3) throw new ApiError(404, "not_found", "No such endpoint.", { endpoints: `${SITE}/api/v1` });

  switch (resource) {
    case "stats":
      if (id) break;
      params.rejectUnknown();
      return json(await stats());
    case "papers":
      if (!id) return json(await listPapers(url, params));
      if (sub) break;
      params.rejectUnknown();
      return json(await getPaper(pathId(id, "paper")));
    case "curves":
      if (!id) return json(await listCurves(url, params));
      if (sub === "data") return curveData(url, pathId(id, "curve"), params);
      if (sub) break;
      params.rejectUnknown();
      return json(await getCurve(pathId(id, "curve")));
    case "figures":
      if (!id) break;
      params.rejectUnknown();
      if (sub === "data") return figureData(pathId(id, "figure"));
      if (sub) break;
      return json(await getFigure(pathId(id, "figure")));
  }
  throw new ApiError(404, "not_found", "No such endpoint.", { endpoints: `${SITE}/api/v1` });
}

export default {
  async fetch(request: Request): Promise<Response> {
    if (request.method === "OPTIONS") {
      return new Response(null, { status: 204, headers: { ...baseHeaders(), "Access-Control-Max-Age": "86400" } });
    }
    if (request.method !== "GET" && request.method !== "HEAD") {
      return errorResponse(new ApiError(405, "method_not_allowed", "This API is read-only. Use GET."));
    }
    try {
      return await route(request);
    } catch (err) {
      if (err instanceof ApiError) return errorResponse(err);
      console.error("unhandled", err);
      return errorResponse(new ApiError(500, "internal_error", "Unexpected error."));
    }
  },
};

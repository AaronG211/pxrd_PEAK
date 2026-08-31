import { DEMO_PAPERS } from "../demoData";
import { supabase } from "./supabase";
export { isSupabaseConfigured } from "./supabase";
import {
  buildMaterialLabelIndex,
  EMPTY_MATERIAL_LABEL_INDEX,
} from "./materialGroups";
import type { MaterialLabelEntry, MaterialLabelIndex } from "./materialGroups";
import {
  RATIO_BAND_BOUNDS,
  curveMatchesTarget,
  searchableStatuses,
  serverWindowHalfWidth,
} from "./peakSearch";
import type { CurveSearchCriteria } from "./peakSearch";
import type {
  CurveRole,
  FigureQualityStatus,
  FigureVerificationStatus,
  FirstPeakStatus,
  PaperDetail,
  PaperSummary,
  PublicationStatus,
  PxrdCurve,
  PxrdFigure,
  StackingHumpStatus,
  WavelengthSource,
} from "../types";

type DbCurve = {
  id: string;
  series_id: string;
  label: string;
  material_name: string | null;
  curve_role: PxrdCurve["role"];
  sample_state: string | null;
  two_theta_min: number | null;
  two_theta_max: number | null;
  point_count: number;
  peak_count: number | null;
  data_path: string | null;
  sort_order: number;
  // Present only once the verification-signals migration and a re-import have run.
  two_theta_uncertainty_deg?: number | null;
  trace_confidence?: number | null;
  // Present only once the peak-and-crystallinity migration and a re-import have run.
  first_peak_two_theta_deg?: number | null;
  first_peak_d_angstrom?: number | null;
  first_peak_fwhm_deg?: number | null;
  first_peak_status?: FirstPeakStatus | null;
  first_peak_wavelength_angstrom?: number | null;
  first_peak_wavelength_source?: WavelengthSource | null;
  crystalline_fraction?: number | null;
  stacking_hump_status?: StackingHumpStatus | null;
  stacking_hump_center_deg?: number | null;
  stacking_hump_fwhm_deg?: number | null;
  intensity_ratio_100_001?: number | null;
  descriptor_version?: string | null;
};

type DbFigure = {
  id: string;
  figure_label: string | null;
  page_number: number | null;
  caption: string | null;
  crop_path: string;
  digitized_plot_path: string;
  overlay_path: string | null;
  quality_status: PxrdFigure["qualityStatus"];
  sort_order: number;
  pxrd_curves: DbCurve[] | null;
  // Present only once the verification-signals migration and a re-import have run.
  verification_status?: FigureVerificationStatus | null;
  axis_agreement_deg?: number | null;
  axis_rmse_deg?: number | null;
  axis_tick_count?: number | null;
  series_detected?: number | null;
  series_digitized?: number | null;
  series_omitted_computed?: number | null;
};

type DbPaper = {
  id: string;
  paper_number: string;
  doi: string | null;
  title: string;
  authors: string | null;
  journal: string | null;
  publication_year: number | null;
  source_url: string | null;
  pxrd_figures: DbFigure[] | null;
  // Present only once the publication-status migration and a backfill have run.
  publication_status?: PublicationStatus | null;
  publication_status_notice_doi?: string | null;
};

type DbPaperSummary = {
  id: string;
  paper_number: string;
  doi: string | null;
  title: string;
  authors: string | null;
  journal: string | null;
  publication_year: number | null;
  figure_count: number;
  curve_count: number;
  material_count: number;
  publication_status?: PublicationStatus | null;
  publication_status_notice_doi?: string | null;
};

type DbFigureFacet = {
  id: string;
  paper_id: string;
  quality_status: FigureQualityStatus;
};

type DbCurveFacet = {
  id: string;
  figure_id: string;
  material_name: string | null;
  curve_role: CurveRole;
  sample_state: string | null;
};

type PaperFacetAccumulator = {
  materialNames: Set<string>;
  curveRoles: Set<CurveRole>;
  sampleStates: Set<string>;
  qualityStatuses: Set<FigureQualityStatus>;
};

const POSTGREST_PAGE_SIZE = 1000;

const PAPER_INDEX_BASE_COLUMNS =
  "id, paper_number, doi, title, authors, journal, publication_year, figure_count, curve_count, material_count";
const PAPER_STATUS_COLUMNS = "publication_status, publication_status_notice_doi";
const FIGURE_VERIFICATION_COLUMNS =
  "verification_status, axis_agreement_deg, axis_rmse_deg, axis_tick_count, series_detected, series_digitized, series_omitted_computed";
const CURVE_FIDELITY_COLUMNS = "two_theta_uncertainty_deg, trace_confidence";
/**
 * Named as ONE group each, and probed as one group, because PostgREST rejects
 * the whole request over a single unknown column. All-or-nothing here means a
 * partial rename upstream hides the feature instead of 400-ing every page.
 */
const CURVE_FIRST_PEAK_COLUMNS =
  "first_peak_two_theta_deg, first_peak_d_angstrom, first_peak_fwhm_deg, first_peak_status, first_peak_wavelength_angstrom, first_peak_wavelength_source";
const CURVE_DESCRIPTOR_COLUMNS =
  "crystalline_fraction, stacking_hump_status, stacking_hump_center_deg, stacking_hump_fwhm_deg, intensity_ratio_100_001, descriptor_version";

/**
 * The publication-status and verification-signal columns arrive in a separate
 * migration owned by the import pipeline. PostgREST rejects the WHOLE request
 * when a select names a column that does not exist, so the frontend probes for
 * them once per session and degrades to the legacy projection instead of taking
 * the page down during the window between the two deploys.
 */
const columnProbes = new Map<string, Promise<boolean>>();

function hasColumn(table: string, column: string): Promise<boolean> {
  const key = `${table}.${column}`;
  const existing = columnProbes.get(key);
  if (existing) return existing;
  const probe = (async () => {
    if (!supabase) return false;
    const { error } = await supabase.from(table).select(column).limit(1);
    return !error;
  })();
  columnProbes.set(key, probe);
  return probe;
}

/** Group probe: true only when EVERY column in the list resolves. */
function hasColumns(table: string, columns: string): Promise<boolean> {
  return hasColumn(table, columns);
}

function cleanFacet(value: string | null): string | null {
  const cleaned = value?.replace(/\s+/g, " ").trim();
  return cleaned || null;
}

function hasResolvedTitle(title: string, id: string, doi: string | null): boolean {
  const normalize = (value: string) =>
    value.trim().toLowerCase().replace(/^https?:\/\/(dx\.)?doi\.org\//, "").replace("/", "_");
  const normalizedTitle = normalize(title);
  return normalizedTitle !== normalize(id) && (!doi || normalizedTitle !== normalize(doi));
}

function numberOrNull(value: number | null | undefined): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function publicationStatus(value: PublicationStatus | null | undefined): PublicationStatus {
  return value ?? "active";
}

function emptyFacets(): PaperFacetAccumulator {
  return {
    materialNames: new Set(),
    curveRoles: new Set(),
    sampleStates: new Set(),
    qualityStatuses: new Set(),
  };
}

function sortedValues(values: Set<string>): string[] {
  return [...values].sort((a, b) => a.localeCompare(b, undefined, { sensitivity: "base" }));
}

function summarizeFacets(facets: PaperFacetAccumulator) {
  return {
    materialNames: sortedValues(facets.materialNames),
    curveRoles: [...facets.curveRoles].sort(),
    sampleStates: sortedValues(facets.sampleStates),
    qualityStatuses: [...facets.qualityStatuses].sort(),
  };
}

function demoFacets(paper: PaperDetail) {
  const facets = emptyFacets();
  for (const figure of paper.figures) {
    facets.qualityStatuses.add(figure.qualityStatus);
    for (const curve of figure.curves) {
      const material = cleanFacet(curve.materialName);
      const state = cleanFacet(curve.sampleState);
      if (material) facets.materialNames.add(material);
      if (state) facets.sampleStates.add(state);
      facets.curveRoles.add(curve.role);
    }
  }
  return summarizeFacets(facets);
}

async function fetchAllPaperRows(): Promise<DbPaperSummary[]> {
  const withStatus = await hasColumn("pxrd_paper_index", "publication_status");
  const columns = withStatus
    ? `${PAPER_INDEX_BASE_COLUMNS}, ${PAPER_STATUS_COLUMNS}`
    : PAPER_INDEX_BASE_COLUMNS;
  const rows: DbPaperSummary[] = [];
  for (let from = 0; ; from += POSTGREST_PAGE_SIZE) {
    const { data, error } = await supabase!
      .from("pxrd_paper_index")
      .select(columns)
      .order("paper_number")
      .range(from, from + POSTGREST_PAGE_SIZE - 1);
    if (error) throw error;
    const page = (data ?? []) as unknown as DbPaperSummary[];
    rows.push(...page);
    if (page.length < POSTGREST_PAGE_SIZE) break;
  }
  return rows;
}

async function fetchAllFigureFacets(): Promise<DbFigureFacet[]> {
  const rows: DbFigureFacet[] = [];
  for (let from = 0; ; from += POSTGREST_PAGE_SIZE) {
    const { data, error } = await supabase!
      .from("pxrd_figures")
      .select("id, paper_id, quality_status")
      .order("id")
      .range(from, from + POSTGREST_PAGE_SIZE - 1);
    if (error) throw error;
    const page = (data ?? []) as DbFigureFacet[];
    rows.push(...page);
    if (page.length < POSTGREST_PAGE_SIZE) break;
  }
  return rows;
}

async function fetchAllCurveFacets(): Promise<DbCurveFacet[]> {
  const rows: DbCurveFacet[] = [];
  for (let from = 0; ; from += POSTGREST_PAGE_SIZE) {
    const { data, error } = await supabase!
      .from("pxrd_curves")
      .select("id, figure_id, material_name, curve_role, sample_state")
      .order("id")
      .range(from, from + POSTGREST_PAGE_SIZE - 1);
    if (error) throw error;
    const page = (data ?? []) as DbCurveFacet[];
    rows.push(...page);
    if (page.length < POSTGREST_PAGE_SIZE) break;
  }
  return rows;
}

function assetUrl(path: string | null): string {
  if (!path) return "";
  if (/^(https?:)?\//.test(path)) return path;
  if (!supabase) return path;
  return supabase.storage.from("pxrd-assets").getPublicUrl(path).data.publicUrl;
}

function mapCurve(curve: DbCurve): PxrdCurve {
  return {
    id: curve.id,
    seriesId: curve.series_id,
    label: curve.label,
    materialName: curve.material_name,
    role: curve.curve_role,
    sampleState: curve.sample_state,
    twoThetaMin: curve.two_theta_min,
    twoThetaMax: curve.two_theta_max,
    pointCount: curve.point_count,
    peakCount: curve.peak_count,
    dataUrl: curve.data_path ? assetUrl(curve.data_path) : null,
    twoThetaUncertaintyDeg: numberOrNull(curve.two_theta_uncertainty_deg),
    traceConfidence: numberOrNull(curve.trace_confidence),
    firstPeakTwoThetaDeg: numberOrNull(curve.first_peak_two_theta_deg),
    firstPeakDAngstrom: numberOrNull(curve.first_peak_d_angstrom),
    firstPeakFwhmDeg: numberOrNull(curve.first_peak_fwhm_deg),
    firstPeakStatus: curve.first_peak_status ?? null,
    firstPeakWavelengthAngstrom: numberOrNull(curve.first_peak_wavelength_angstrom),
    firstPeakWavelengthSource: curve.first_peak_wavelength_source ?? null,
    crystallineFraction: numberOrNull(curve.crystalline_fraction),
    stackingHumpStatus: curve.stacking_hump_status ?? null,
    stackingHumpCenterDeg: numberOrNull(curve.stacking_hump_center_deg),
    stackingHumpFwhmDeg: numberOrNull(curve.stacking_hump_fwhm_deg),
    intensityRatio100001: numberOrNull(curve.intensity_ratio_100_001),
    descriptorVersion: curve.descriptor_version ?? null,
  };
}

function mapFigure(figure: DbFigure): PxrdFigure {
  return {
    id: figure.id,
    figureLabel: figure.figure_label,
    pageNumber: figure.page_number,
    caption: figure.caption,
    sourceCropUrl: assetUrl(figure.crop_path),
    digitizedPlotUrl: assetUrl(figure.digitized_plot_path),
    overlayUrl: figure.overlay_path ? assetUrl(figure.overlay_path) : null,
    qualityStatus: figure.quality_status,
    verificationStatus: figure.verification_status ?? null,
    axisAgreementDeg: numberOrNull(figure.axis_agreement_deg),
    axisRmseDeg: numberOrNull(figure.axis_rmse_deg),
    axisTickCount: numberOrNull(figure.axis_tick_count),
    seriesDetected: numberOrNull(figure.series_detected),
    seriesDigitized: numberOrNull(figure.series_digitized),
    seriesOmittedComputed: numberOrNull(figure.series_omitted_computed),
    curves: [...(figure.pxrd_curves ?? [])]
      .sort((a, b) => a.sort_order - b.sort_order)
      .map(mapCurve),
  };
}

function mapPaper(row: DbPaper): PaperDetail {
  const figures = [...(row.pxrd_figures ?? [])]
    .sort((a, b) => a.sort_order - b.sort_order)
    .map(mapFigure);

  const materialNames = new Set(
    figures.flatMap((figure) =>
      figure.curves.flatMap((curve) => (curve.materialName ? [curve.materialName] : [])),
    ),
  );
  const facets = emptyFacets();
  for (const figure of figures) {
    facets.qualityStatuses.add(figure.qualityStatus);
    for (const curve of figure.curves) {
      const material = cleanFacet(curve.materialName);
      const state = cleanFacet(curve.sampleState);
      if (material) facets.materialNames.add(material);
      if (state) facets.sampleStates.add(state);
      facets.curveRoles.add(curve.role);
    }
  }

  return {
    id: row.id,
    paperNumber: row.paper_number,
    doi: row.doi,
    title: row.title,
    authors: row.authors,
    journal: row.journal,
    year: row.publication_year,
    sourceUrl: row.source_url,
    figures,
    figureCount: figures.length,
    curveCount: figures.reduce((total, figure) => total + figure.curves.length, 0),
    materialCount: materialNames.size,
    ...summarizeFacets(facets),
    hasResolvedTitle: hasResolvedTitle(row.title, row.id, row.doi),
    publicationStatus: publicationStatus(row.publication_status),
    publicationStatusNoticeDoi: row.publication_status_notice_doi ?? null,
    // The detail page reads label links from getMaterialLabelIndex(), which knows
    // the whole collection; one paper's own payload cannot know what it shares.
    sharedLabelKeys: [],
  };
}

/* -------------------------------------------------------------------------- */
/* Cross-paper material label index                                            */
/* -------------------------------------------------------------------------- */

/**
 * Session-scoped, because it is a property of the whole collection rather than
 * of any one page. `fetchPapers` primes it for free out of the curve facets it
 * already downloads; a reader who lands straight on a paper page pays one
 * dedicated fetch instead (measured live: 2,890 rows, 259 kB raw / 20.8 kB
 * gzip, 3 requests, ~370 ms).
 */
let materialLabelIndexPromise: Promise<MaterialLabelIndex> | null = null;

function demoLabelEntries(): MaterialLabelEntry[] {
  return DEMO_PAPERS.flatMap((paper) =>
    paper.figures.flatMap((figure) =>
      figure.curves.map((curve) => ({
        materialName: curve.materialName,
        paperId: paper.id,
      })),
    ),
  );
}

export function getMaterialLabelIndex(): Promise<MaterialLabelIndex> {
  if (materialLabelIndexPromise) return materialLabelIndexPromise;
  const client = supabase;
  const pending = (async (): Promise<MaterialLabelIndex> => {
    if (!client) return buildMaterialLabelIndex(demoLabelEntries());
    const entries: MaterialLabelEntry[] = [];
    for (let from = 0; ; from += POSTGREST_PAGE_SIZE) {
      const { data, error } = await client
        .from("pxrd_curves")
        .select("material_name, pxrd_figures!inner(paper_id)")
        .not("material_name", "is", null)
        .order("id")
        .range(from, from + POSTGREST_PAGE_SIZE - 1);
      if (error) throw error;
      const page = (data ?? []) as unknown as Array<{
        material_name: string | null;
        pxrd_figures: { paper_id: string } | null;
      }>;
      for (const row of page) {
        const paperId = row.pxrd_figures?.paper_id;
        if (paperId) entries.push({ materialName: row.material_name, paperId });
      }
      if (page.length < POSTGREST_PAGE_SIZE) break;
    }
    return buildMaterialLabelIndex(entries);
  })();
  materialLabelIndexPromise = pending;
  // A transient failure must not leave the session permanently unable to link.
  pending.catch(() => {
    if (materialLabelIndexPromise === pending) materialLabelIndexPromise = null;
  });
  return pending;
}

export async function fetchPapers(): Promise<PaperSummary[]> {
  if (!supabase) {
    const demoIndex = buildMaterialLabelIndex(demoLabelEntries());
    materialLabelIndexPromise = Promise.resolve(demoIndex);
    return DEMO_PAPERS.map((paper) => ({
      ...paper,
      ...demoFacets(paper),
      hasResolvedTitle: hasResolvedTitle(paper.title, paper.id, paper.doi),
      sharedLabelKeys: demoIndex.sharedByPaper.get(paper.id) ?? [],
    }));
  }

  const [paperRows, figureRows, curveRows] = await Promise.all([
    fetchAllPaperRows(),
    fetchAllFigureFacets(),
    fetchAllCurveFacets(),
  ]);
  const figureToPaper = new Map(figureRows.map((figure) => [figure.id, figure.paper_id]));
  const facetsByPaper = new Map<string, PaperFacetAccumulator>();
  for (const row of paperRows) facetsByPaper.set(row.id, emptyFacets());
  for (const figure of figureRows) {
    facetsByPaper.get(figure.paper_id)?.qualityStatuses.add(figure.quality_status);
  }
  const labelEntries: MaterialLabelEntry[] = [];
  for (const curve of curveRows) {
    const paperId = figureToPaper.get(curve.figure_id);
    if (!paperId) continue;
    labelEntries.push({ materialName: curve.material_name, paperId });
    const facets = facetsByPaper.get(paperId);
    if (!facets) continue;
    const material = cleanFacet(curve.material_name);
    const state = cleanFacet(curve.sample_state);
    if (material) facets.materialNames.add(material);
    if (state) facets.sampleStates.add(state);
    facets.curveRoles.add(curve.curve_role);
  }
  // Free: these are the same rows the facet pass already paid for.
  const labelIndex = buildMaterialLabelIndex(labelEntries);
  materialLabelIndexPromise = Promise.resolve(labelIndex);

  return paperRows.map((row) => ({
    id: row.id,
    paperNumber: row.paper_number,
    doi: row.doi,
    title: row.title,
    authors: row.authors,
    journal: row.journal,
    year: row.publication_year,
    figureCount: row.figure_count,
    curveCount: row.curve_count,
    materialCount: row.material_count,
    ...summarizeFacets(facetsByPaper.get(row.id) ?? emptyFacets()),
    hasResolvedTitle: hasResolvedTitle(row.title, row.id, row.doi),
    publicationStatus: publicationStatus(row.publication_status),
    publicationStatusNoticeDoi: row.publication_status_notice_doi ?? null,
    sharedLabelKeys: labelIndex.sharedByPaper.get(row.id) ?? [],
  }));
}

export async function fetchPaper(id: string): Promise<PaperDetail> {
  if (!supabase) {
    const paper = DEMO_PAPERS.find((item) => item.id === id);
    if (!paper) throw new Error("Paper not found");
    return paper;
  }

  const [withStatus, withVerification, withFidelity, withFirstPeak, withDescriptors] =
    await Promise.all([
      hasColumn("pxrd_papers", "publication_status"),
      hasColumn("pxrd_figures", "verification_status"),
      hasColumn("pxrd_curves", "two_theta_uncertainty_deg"),
      hasColumns("pxrd_curves", CURVE_FIRST_PEAK_COLUMNS),
      hasColumns("pxrd_curves", CURVE_DESCRIPTOR_COLUMNS),
    ]);

  const { data, error } = await supabase
    .from("pxrd_papers")
    .select(`
      id, paper_number, doi, title, authors, journal, publication_year, source_url,
      ${withStatus ? `${PAPER_STATUS_COLUMNS},` : ""}
      pxrd_figures (
        id, figure_label, page_number, caption, crop_path, digitized_plot_path,
        overlay_path, quality_status, sort_order,
        ${withVerification ? `${FIGURE_VERIFICATION_COLUMNS},` : ""}
        pxrd_curves (
          id, series_id, label, material_name, curve_role, sample_state,
          two_theta_min, two_theta_max, point_count, peak_count, data_path, sort_order
          ${withFidelity ? `, ${CURVE_FIDELITY_COLUMNS}` : ""}
          ${withFirstPeak ? `, ${CURVE_FIRST_PEAK_COLUMNS}` : ""}
          ${withDescriptors ? `, ${CURVE_DESCRIPTOR_COLUMNS}` : ""}
        )
      )
    `)
    .eq("id", id)
    .single();

  if (error) throw error;
  return mapPaper(data as unknown as DbPaper);
}

/* -------------------------------------------------------------------------- */
/* Curve-level search: first peak position and crystallinity                    */
/* -------------------------------------------------------------------------- */

/**
 * Why this is a server query and not another client-side predicate.
 *
 * HomePage filters an already-downloaded array, which is right for paper-level
 * facets. Peak and descriptor values are per CURVE, and recon measured the cost
 * of carrying them in the bulk fetch: adding the four first-peak scalars takes
 * the cold HomePage payload from ~200 kB to ~252 kB gzip (+26%), and adding the
 * crystallinity scalars as well takes it to ~283 kB (+42%) — on every visitor,
 * to serve a panel most never open. Meanwhile these queries are extremely
 * selective: a single-peak search at ±0.1° returns 230 of 3,815 curves. Paying
 * 83 kB to filter locally down to 230 rows is backwards by about two orders of
 * magnitude, so the filter state lives in HomePage like every other filter and
 * only the row matching happens server-side.
 */
export interface CurveSearchCapabilities {
  /** Whether the first-peak column group exists in the published schema. */
  firstPeak: boolean;
  /** Whether the crystallinity descriptor column group exists. */
  descriptors: boolean;
  /** Whether per-curve axis uncertainty exists, for the per-curve widening. */
  axisUncertainty: boolean;
  /**
   * Whether pxrd_figures.verification_status exists, so a peak search can say
   * how many of its matches sit on a disputed axis. Absent before the 20260828
   * migration, in which case the search runs but discloses nothing.
   */
  verificationStatus: boolean;
  statusCounts: Partial<Record<FirstPeakStatus, number>>;
  humpCounts: Partial<Record<StackingHumpStatus | "no_descriptor", number>>;
  ratioCount: number;
}

export const NO_CURVE_SEARCH_CAPABILITIES: CurveSearchCapabilities = {
  firstPeak: false,
  descriptors: false,
  axisUncertainty: false,
  verificationStatus: false,
  statusCounts: {},
  humpCounts: {},
  ratioCount: 0,
};

export interface CurveSearchResult {
  /** paperId -> how many of that paper's curves matched. */
  papers: Map<string, number>;
  curveCount: number;
  /**
   * How many of the matched curves sit on a figure whose 2θ axis was arbitrated
   * or never cross-validated. Reported so a reader is never told "N curves
   * match ±0.1°" about positions the pipeline itself disputes.
   */
  disputedAxisCurveCount: number;
  /** True when the row cap was hit, so the result is a floor, not a total. */
  truncated: boolean;
}

/**
 * The importer's sentinel for "no descriptor row exists for this curve". The
 * column is also nullable, so both spellings have to be handled everywhere.
 */
const NO_DESCRIPTOR_SENTINEL = "not_computed";

/** Far above the 3,815 published curves; a guard, not a page size. */
const CURVE_SEARCH_ROW_CAP = 20000;

type CountMatch =
  | { kind: "eq"; value: string }
  | { kind: "is-null" }
  | { kind: "not-null" };

async function countCurves(column: string, match: CountMatch): Promise<number> {
  if (!supabase) return 0;
  const base = supabase.from("pxrd_curves").select("id", { count: "exact", head: true });
  const query =
    match.kind === "eq"
      ? base.eq(column, match.value)
      : match.kind === "is-null"
        ? base.is(column, null)
        : base.not(column, "is", null);
  const { count, error } = await query;
  // A count that cannot be taken is reported as zero, which hides the option
  // rather than offering one whose size we cannot state.
  if (error) return 0;
  return count ?? 0;
}

/**
 * Probe once per session, exactly like the publication-status columns.
 *
 * When a group is absent the matching controls are not rendered at all. That
 * follows the rule the role dropdown already sets: never offer a filter that
 * cannot match anything. The counts double as the honest option labels — a
 * reader should be able to see that turning off the `ok` gate adds 1,029 curves
 * before they turn it off.
 */
/**
 * Capabilities of the offline demo snapshot, derived from the fixture itself.
 *
 * Returning "no columns" here would hide the peak and crystallinity controls in
 * the one environment that can always show them: the live database does not
 * carry these columns until the 20260830 migration is run, so without this the
 * feature would be unreachable everywhere and untestable by anyone.
 */
function demoCurveSearchCapabilities(): CurveSearchCapabilities {
  const curves = DEMO_PAPERS.flatMap((paper) =>
    paper.figures.flatMap((figure) => figure.curves),
  );
  const statusCounts: Partial<Record<FirstPeakStatus, number>> = {};
  const humpCounts: Partial<Record<StackingHumpStatus | "no_descriptor", number>> = {};
  let ratioCount = 0;
  for (const curve of curves) {
    if (curve.firstPeakStatus !== null) {
      statusCounts[curve.firstPeakStatus] = (statusCounts[curve.firstPeakStatus] ?? 0) + 1;
    }
    const hump = curve.stackingHumpStatus ?? "no_descriptor";
    humpCounts[hump] = (humpCounts[hump] ?? 0) + 1;
    if (curve.intensityRatio100001 !== null) ratioCount += 1;
  }
  return {
    firstPeak: curves.some((curve) => curve.firstPeakTwoThetaDeg !== null),
    descriptors: curves.some((curve) => curve.crystallineFraction !== null),
    axisUncertainty: curves.some((curve) => curve.twoThetaUncertaintyDeg !== null),
    // The demo has no figure-level verification column to consult, so a demo
    // search discloses no axis dispute rather than asserting there is none.
    verificationStatus: false,
    statusCounts,
    humpCounts,
    ratioCount,
  };
}

export async function fetchCurveSearchCapabilities(): Promise<CurveSearchCapabilities> {
  if (!supabase) return demoCurveSearchCapabilities();
  const [firstPeak, descriptors, axisUncertainty, verificationStatus] = await Promise.all([
    hasColumns("pxrd_curves", CURVE_FIRST_PEAK_COLUMNS),
    hasColumns("pxrd_curves", CURVE_DESCRIPTOR_COLUMNS),
    hasColumn("pxrd_curves", "two_theta_uncertainty_deg"),
    hasColumn("pxrd_figures", "verification_status"),
  ]);
  if (!firstPeak && !descriptors) {
    return { ...NO_CURVE_SEARCH_CAPABILITIES, axisUncertainty, verificationStatus };
  }

  const statusKeys: FirstPeakStatus[] = [
    "ok",
    "low_confidence",
    "truncated_at_window_start",
    "no_bragg_peak",
  ];
  const humpKeys: StackingHumpStatus[] = [
    "hump_detected",
    "no_hump_detected",
    "window_not_covered",
  ];

  const [statusValues, humpValues, ratioCount] = await Promise.all([
    firstPeak
      ? Promise.all(
          statusKeys.map((status) =>
            countCurves("first_peak_status", { kind: "eq", value: status }),
          ),
        )
      : Promise.resolve<number[]>([]),
    descriptors
      ? Promise.all([
          ...humpKeys.map((status) =>
            countCurves("stacking_hump_status", { kind: "eq", value: status }),
          ),
          countCurves("stacking_hump_status", { kind: "is-null" }),
          countCurves("stacking_hump_status", {
            kind: "eq",
            value: NO_DESCRIPTOR_SENTINEL,
          }),
        ])
      : Promise.resolve<number[]>([]),
    descriptors
      ? countCurves("intensity_ratio_100_001", { kind: "not-null" })
      : Promise.resolve(0),
  ]);

  const statusCounts: Partial<Record<FirstPeakStatus, number>> = {};
  statusKeys.forEach((key, i) => {
    if (statusValues[i] > 0) statusCounts[key] = statusValues[i];
  });
  const humpCounts: Partial<Record<StackingHumpStatus | "no_descriptor", number>> = {};
  humpKeys.forEach((key, i) => {
    if (humpValues[i] > 0) humpCounts[key] = humpValues[i];
  });
  // NULL rows and sentinel rows are the same option, so their counts add.
  const noDescriptor = (humpValues[humpKeys.length] ?? 0)
    + (humpValues[humpKeys.length + 1] ?? 0);
  if (noDescriptor > 0) humpCounts.no_descriptor = noDescriptor;

  return {
    firstPeak,
    descriptors,
    axisUncertainty,
    verificationStatus,
    statusCounts,
    humpCounts,
    ratioCount,
  };
}

type DbCurveSearchRow = {
  id: string;
  first_peak_two_theta_deg?: number | null;
  two_theta_uncertainty_deg?: number | null;
  pxrd_figures: { paper_id: string; verification_status?: string | null } | null;
};

/**
 * Figures whose 2θ axis the two calibration methods did not agree on, or that
 * were never cross-checked. A match on such a figure is a match against a
 * position the pipeline itself disputes, so it is counted and disclosed rather
 * than silently mixed into the result.
 */
const DISPUTED_AXIS_STATUSES = new Set(["axis_arbitrated", "axis_unverified"]);

/**
 * Return the papers holding at least one curve that matches, with the per-paper
 * match count.
 *
 * The server window is deliberately WIDER than the reader's tolerance by
 * `MAX_AXIS_UNCERTAINTY_DEG`, because the final rule widens per curve to that
 * curve's own `two_theta_uncertainty_deg`. Filtering at the exact tolerance in
 * SQL would drop curves the rule is supposed to keep.
 */
export async function searchCurves(
  criteria: CurveSearchCriteria,
  capabilities: CurveSearchCapabilities,
  signal?: AbortSignal,
): Promise<CurveSearchResult> {
  const papers = new Map<string, number>();
  if (!supabase) {
    return { papers, curveCount: 0, disputedAxisCurveCount: 0, truncated: false };
  }

  const columns = [
    "id",
    criteria.targetTwoThetaDeg !== null ? "first_peak_two_theta_deg" : null,
    criteria.targetTwoThetaDeg !== null && capabilities.axisUncertainty
      ? "two_theta_uncertainty_deg"
      : null,
    capabilities.verificationStatus
      ? "pxrd_figures!inner(paper_id, verification_status)"
      : "pxrd_figures!inner(paper_id)",
  ]
    .filter(Boolean)
    .join(", ");

  let curveCount = 0;
  let disputedAxisCurveCount = 0;
  let truncated = false;

  for (let from = 0; from < CURVE_SEARCH_ROW_CAP; from += POSTGREST_PAGE_SIZE) {
    let query = supabase.from("pxrd_curves").select(columns);
    if (criteria.targetTwoThetaDeg !== null) {
      const half = serverWindowHalfWidth(criteria.toleranceDeg);
      query = query
        .in("first_peak_status", searchableStatuses(criteria.includeLowConfidence))
        .gte("first_peak_two_theta_deg", criteria.targetTwoThetaDeg - half)
        .lte("first_peak_two_theta_deg", criteria.targetTwoThetaDeg + half);
    }
    if (criteria.humpFilter === "no_descriptor") {
      // Two encodings of one fact: the importer may write the sentinel or leave
      // the column NULL, and a reader asking for "no descriptor" means both.
      query = query.or(
        `stacking_hump_status.is.null,stacking_hump_status.eq.${NO_DESCRIPTOR_SENTINEL}`,
      );
    } else if (criteria.humpFilter !== "all") {
      query = query.eq("stacking_hump_status", criteria.humpFilter);
    }
    if (criteria.ratioBand !== "all") {
      const bounds = RATIO_BAND_BOUNDS[criteria.ratioBand];
      if (bounds.min !== null) query = query.gt("intensity_ratio_100_001", bounds.min);
      if (bounds.max !== null) query = query.lte("intensity_ratio_100_001", bounds.max);
    }
    const paged = query.order("id").range(from, from + POSTGREST_PAGE_SIZE - 1);
    const { data, error } = await (signal ? paged.abortSignal(signal) : paged);
    if (error) throw error;
    const page = (data ?? []) as unknown as DbCurveSearchRow[];

    for (const row of page) {
      const paperId = row.pxrd_figures?.paper_id;
      if (!paperId) continue;
      if (
        criteria.targetTwoThetaDeg !== null
        && !curveMatchesTarget(
          row.first_peak_two_theta_deg,
          row.two_theta_uncertainty_deg,
          criteria.targetTwoThetaDeg,
          criteria.toleranceDeg,
        )
      ) {
        continue;
      }
      curveCount += 1;
      const verification = row.pxrd_figures?.verification_status;
      if (verification != null && DISPUTED_AXIS_STATUSES.has(verification)) {
        disputedAxisCurveCount += 1;
      }
      papers.set(paperId, (papers.get(paperId) ?? 0) + 1);
    }

    if (page.length < POSTGREST_PAGE_SIZE) break;
    if (from + POSTGREST_PAGE_SIZE >= CURVE_SEARCH_ROW_CAP) truncated = true;
  }

  return { papers, curveCount, disputedAxisCurveCount, truncated };
}

export { EMPTY_MATERIAL_LABEL_INDEX };

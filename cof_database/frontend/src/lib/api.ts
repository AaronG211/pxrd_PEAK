import { DEMO_PAPERS } from "../demoData";
import { supabase } from "./supabase";
export { isSupabaseConfigured } from "./supabase";
import type {
  CurveRole,
  FigureQualityStatus,
  PaperDetail,
  PaperSummary,
  PxrdCurve,
  PxrdFigure,
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
  const rows: DbPaperSummary[] = [];
  for (let from = 0; ; from += POSTGREST_PAGE_SIZE) {
    const { data, error } = await supabase!
      .from("pxrd_paper_index")
      .select("id, paper_number, doi, title, authors, journal, publication_year, figure_count, curve_count, material_count")
      .order("paper_number")
      .range(from, from + POSTGREST_PAGE_SIZE - 1);
    if (error) throw error;
    const page = (data ?? []) as DbPaperSummary[];
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
  };
}

function mapPaper(row: DbPaper): PaperDetail {
  const figures = [...(row.pxrd_figures ?? [])]
    .sort((a, b) => a.sort_order - b.sort_order)
    .map((figure): PxrdFigure => ({
    id: figure.id,
    figureLabel: figure.figure_label,
    pageNumber: figure.page_number,
    caption: figure.caption,
    sourceCropUrl: assetUrl(figure.crop_path),
    digitizedPlotUrl: assetUrl(figure.digitized_plot_path),
    overlayUrl: figure.overlay_path ? assetUrl(figure.overlay_path) : null,
    qualityStatus: figure.quality_status,
    curves: [...(figure.pxrd_curves ?? [])]
      .sort((a, b) => a.sort_order - b.sort_order)
      .map(mapCurve),
  }));

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
  };
}

export async function fetchPapers(): Promise<PaperSummary[]> {
  if (!supabase) {
    return DEMO_PAPERS.map((paper) => ({
      ...paper,
      ...demoFacets(paper),
      hasResolvedTitle: hasResolvedTitle(paper.title, paper.id, paper.doi),
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
  for (const curve of curveRows) {
    const paperId = figureToPaper.get(curve.figure_id);
    if (!paperId) continue;
    const facets = facetsByPaper.get(paperId);
    if (!facets) continue;
    const material = cleanFacet(curve.material_name);
    const state = cleanFacet(curve.sample_state);
    if (material) facets.materialNames.add(material);
    if (state) facets.sampleStates.add(state);
    facets.curveRoles.add(curve.curve_role);
  }

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
  }));
}

export async function fetchPaper(id: string): Promise<PaperDetail> {
  if (!supabase) {
    const paper = DEMO_PAPERS.find((item) => item.id === id);
    if (!paper) throw new Error("Paper not found");
    return paper;
  }

  const { data, error } = await supabase
    .from("pxrd_papers")
    .select(`
      id, paper_number, doi, title, authors, journal, publication_year, source_url,
      pxrd_figures (
        id, figure_label, page_number, caption, crop_path, digitized_plot_path,
        overlay_path, quality_status, sort_order,
        pxrd_curves (
          id, series_id, label, material_name, curve_role, sample_state,
          two_theta_min, two_theta_max, point_count, peak_count, data_path, sort_order
        )
      )
    `)
    .eq("id", id)
    .single();

  if (error) throw error;
  return mapPaper(data as DbPaper);
}

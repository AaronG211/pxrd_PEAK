import { createClient } from "@supabase/supabase-js";
import { DEMO_PAPERS } from "../demoData";
import type { PaperDetail, PaperSummary, PxrdCurve, PxrdFigure } from "../types";

const supabaseUrl = import.meta.env.VITE_SUPABASE_URL?.trim();
const supabaseAnonKey = import.meta.env.VITE_SUPABASE_ANON_KEY?.trim();

export const isSupabaseConfigured = Boolean(supabaseUrl && supabaseAnonKey);

const supabase = isSupabaseConfigured
  ? createClient(supabaseUrl as string, supabaseAnonKey as string)
  : null;

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
  };
}

export async function fetchPapers(): Promise<PaperSummary[]> {
  if (!supabase) return DEMO_PAPERS;

  const { data, error } = await supabase
    .from("pxrd_paper_index")
    .select("id, paper_number, doi, title, authors, journal, publication_year, figure_count, curve_count, material_count")
    .order("paper_number");

  if (error) throw error;
  return ((data ?? []) as DbPaperSummary[]).map((row) => ({
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

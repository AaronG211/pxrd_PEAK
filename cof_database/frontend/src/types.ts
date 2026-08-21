export type CurveRole =
  | "experimental"
  | "simulated"
  | "refined"
  | "difference"
  | "reference"
  | "unclassified";

export interface PxrdCurve {
  id: string;
  seriesId: string;
  label: string;
  materialName: string | null;
  role: CurveRole;
  sampleState: string | null;
  twoThetaMin: number | null;
  twoThetaMax: number | null;
  pointCount: number;
  peakCount: number | null;
  dataUrl: string | null;
}

export interface PxrdFigure {
  id: string;
  figureLabel: string | null;
  pageNumber: number | null;
  caption: string | null;
  sourceCropUrl: string;
  digitizedPlotUrl: string;
  overlayUrl: string | null;
  qualityStatus: "reviewed" | "pending" | "flagged";
  curves: PxrdCurve[];
}

export interface PaperSummary {
  id: string;
  paperNumber: string;
  doi: string | null;
  title: string;
  authors: string | null;
  journal: string | null;
  year: number | null;
  figureCount: number;
  curveCount: number;
  materialCount: number;
}

export interface PaperDetail extends PaperSummary {
  sourceUrl: string | null;
  figures: PxrdFigure[];
}

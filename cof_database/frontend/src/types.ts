export type CurveRole =
  | "experimental"
  | "simulated"
  | "refined"
  | "difference"
  | "reference"
  | "unclassified";

/**
 * Mirrors the CHECK constraint on `pxrd_figures.quality_status`.
 *
 * `"reviewed"` is still a legal database value and is still present on live rows,
 * but no human has reviewed any figure — the importer wrote it unconditionally.
 * The UI therefore never renders it as an approval; see the provenance badge in
 * PaperDetailPage.
 */
export type FigureQualityStatus = "reviewed" | "pending" | "flagged";

/**
 * Outcome of the automated 2θ axis calibration, from `calibration.status` in each
 * figure's result.json. Null until the verification-signals migration and a
 * re-import have landed.
 */
export type FigureVerificationStatus =
  | "axis_cross_validated"
  | "axis_single_method"
  | "axis_arbitrated"
  | "axis_unverified";

/** Publisher lifecycle state of the paper, from Crossref Crossmark data. */
export type PublicationStatus =
  | "active"
  | "retracted"
  | "withdrawn"
  | "concern"
  | "corrected";

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
  qualityStatus: FigureQualityStatus;
  /**
   * Automated verification signals. Every one of these is null until the
   * verification migration plus a re-import have run, and the UI is expected to
   * degrade to "automated extraction, not human reviewed" on its own.
   */
  verificationStatus: FigureVerificationStatus | null;
  axisAgreementDeg: number | null;
  axisRmseDeg: number | null;
  axisTickCount: number | null;
  seriesDetected: number | null;
  seriesDigitized: number | null;
  seriesOmittedComputed: number | null;
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
  materialNames: string[];
  curveRoles: CurveRole[];
  sampleStates: string[];
  qualityStatuses: FigureQualityStatus[];
  hasResolvedTitle: boolean;
  publicationStatus: PublicationStatus;
  publicationStatusNoticeDoi: string | null;
}

export interface PaperDetail extends PaperSummary {
  sourceUrl: string | null;
  figures: PxrdFigure[];
}

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

/**
 * Outcome of the first-peak detector in tools/first_peak.py.
 *
 * `ok` cleared the SNR, persistence, width and edge gates. `low_confidence` is a
 * real peak that is either weak or has evidence of a lower-angle peak outside the
 * plotted window. The last two carry NO position at all, so a search can never
 * reach them and the UI must say so rather than showing them as a miss.
 */
export type FirstPeakStatus =
  | "ok"
  | "low_confidence"
  | "truncated_at_window_start"
  | "no_bragg_peak";

/**
 * Provenance of the wavelength behind `firstPeakDAngstrom`.
 *
 * In the published collection this is `assumed_cu_ka` on every curve that has a
 * first peak at all: exactly one paper in the whole 2,370-paper corpus reports a
 * machine-readable wavelength, and none of its curves survive into the pilot. The
 * value is carried per row so no reader has to trust a sentence in the UI.
 */
export type WavelengthSource = "assumed_cu_ka" | "paper_reported";

/**
 * Three-state availability of the stacking-hump descriptor.
 *
 * `no_hump_detected` is a MEASUREMENT: the 15-35 deg window was plotted and
 * carried no hump, which is what a well-ordered sample looks like.
 * `window_not_covered` is an ABSENCE OF DATA: the figure never plotted that
 * range. Collapsing the two would report missing data as good crystallinity.
 * `not_computed` — and a plain NULL, which the importer may write instead — is a
 * third kind of absence again: no descriptor exists for the curve at all. Both
 * encodings mean the same thing and both must render as an absence, never as a
 * value.
 */
export type StackingHumpStatus =
  | "hump_detected"
  | "no_hump_detected"
  | "window_not_covered"
  | "not_computed";

/** Publisher lifecycle state of the paper, from Crossref Crossmark data. */
export type PublicationStatus =
  | "active"
  /**
   * Crossref was never asked about this paper. The 20260830 migration writes
   * this to every row that predates the publication-status backfill, so it is
   * the DEFAULT state of the live database until that backfill is re-run — it
   * means "unknown", never "fine".
   */
  | "unchecked"
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
  /**
   * Per-trace fidelity numbers from the digitizer. Null until the
   * verification-signals migration and a re-import have run; the plot readout
   * reads the same 2θ uncertainty straight out of the CSV, so it works either way.
   */
  twoThetaUncertaintyDeg: number | null;
  traceConfidence: number | null;
  /**
   * First-peak geometry from tools/first_peak.py. Null until the peak-and-
   * crystallinity migration and a re-import have run.
   *
   * `firstPeakDAngstrom` is NOT a measured d-spacing. It is
   * `firstPeakWavelengthAngstrom / (2 sin theta)` — a deterministic restatement
   * of `firstPeakTwoThetaDeg` under whatever wavelength
   * `firstPeakWavelengthSource` names. Rendering it without that provenance
   * turns an assumption into a measurement.
   */
  firstPeakTwoThetaDeg: number | null;
  firstPeakDAngstrom: number | null;
  firstPeakFwhmDeg: number | null;
  firstPeakStatus: FirstPeakStatus | null;
  firstPeakWavelengthAngstrom: number | null;
  firstPeakWavelengthSource: WavelengthSource | null;
  /**
   * Within-curve crystallinity descriptors from tools/descriptors.py.
   *
   * `crystallineFraction` is the fraction of integrated intensity carried by
   * features narrower than ~4 deg — NOT a degree of crystallinity, and strongly
   * confounded by how much of the pattern the authors chose to plot (pilot
   * medians run 0.852 for windows under 20 deg against 0.564 for 80-100 deg). It
   * is published as a diagnostic to read beside twoThetaMin/twoThetaMax, never
   * as a filter axis.
   *
   * `intensityRatio100001` is scale-free by construction (a height ratio inside
   * one curve) and is the one descriptor here that compares across curves.
   */
  crystallineFraction: number | null;
  stackingHumpStatus: StackingHumpStatus | null;
  stackingHumpCenterDeg: number | null;
  stackingHumpFwhmDeg: number | null;
  intensityRatio100001: number | null;
  descriptorVersion: string | null;
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
  /**
   * Keys of the cross-paper material LABEL groups this paper participates in,
   * from lib/materialGroups. Only groups that passed the specificity gate and
   * actually span more than one paper appear here, so an empty array means
   * "nothing linkable" — it does NOT mean the paper's materials are unique.
   */
  sharedLabelKeys: string[];
}

export interface PaperDetail extends PaperSummary {
  sourceUrl: string | null;
  figures: PxrdFigure[];
}

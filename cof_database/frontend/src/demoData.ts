import { buildMaterialLabelIndex } from "./lib/materialGroups";
import { ASSUMED_WAVELENGTH_ANGSTROM, twoThetaToDSpacing } from "./lib/peakSearch";
import type { FirstPeakStatus, PaperDetail, PxrdCurve, StackingHumpStatus } from "./types";

type DemoPaper = Omit<
  PaperDetail,
  | "materialNames"
  | "curveRoles"
  | "sampleStates"
  | "qualityStatuses"
  | "hasResolvedTitle"
  | "publicationStatus"
  | "publicationStatusNoticeDoi"
  | "sharedLabelKeys"
>;

/**
 * Per-curve peak and descriptor values the demo snapshot carries.
 *
 * The demo exists so the site is inspectable without a database, so it has to
 * exercise the honest-degradation paths as well as the happy one: `firstPeak`
 * left undefined stands for a curve whose peak column is unpopulated, and
 * `hump: "window_not_covered"` stands for a figure that never plotted the
 * 15-35 deg window. Those must render differently from a low value, and the demo
 * is where that is visible without a migration.
 */
interface DemoPeak {
  twoTheta?: number;
  status?: FirstPeakStatus;
  fwhm?: number;
  uncertainty?: number;
  confidence?: number;
  crystallineFraction?: number;
  hump?: StackingHumpStatus;
  humpCenter?: number;
  humpFwhm?: number;
  ratio?: number;
}

function curve(
  seriesId: string,
  label: string,
  role: PxrdCurve["role"],
  materialName: string | null,
  pointCount: number,
  min: number,
  max: number,
  dataUrl: string,
  peak: DemoPeak = {},
): PxrdCurve {
  const twoTheta = peak.twoTheta ?? null;
  return {
    id: seriesId,
    seriesId,
    label,
    materialName,
    role,
    sampleState: role === "experimental" ? "experimental" : "structural model",
    twoThetaMin: min,
    twoThetaMax: max,
    pointCount,
    peakCount: null,
    dataUrl,
    twoThetaUncertaintyDeg: peak.uncertainty ?? null,
    traceConfidence: peak.confidence ?? null,
    firstPeakTwoThetaDeg: twoTheta,
    // Never hand-written: d is always the derived restatement of the angle under
    // the assumed wavelength, exactly as the pipeline computes it.
    firstPeakDAngstrom: twoTheta === null ? null : twoThetaToDSpacing(twoTheta),
    firstPeakFwhmDeg: peak.fwhm ?? null,
    firstPeakStatus: twoTheta === null ? (peak.status ?? null) : (peak.status ?? "ok"),
    firstPeakWavelengthAngstrom: twoTheta === null ? null : ASSUMED_WAVELENGTH_ANGSTROM,
    firstPeakWavelengthSource: twoTheta === null ? null : "assumed_cu_ka",
    crystallineFraction: peak.crystallineFraction ?? null,
    stackingHumpStatus: peak.hump ?? null,
    stackingHumpCenterDeg: peak.hump === "hump_detected" ? (peak.humpCenter ?? null) : null,
    stackingHumpFwhmDeg: peak.hump === "hump_detected" ? (peak.humpFwhm ?? null) : null,
    intensityRatio100001: peak.hump === "hump_detected" ? (peak.ratio ?? null) : null,
    descriptorVersion: peak.hump === undefined ? null : "v0",
  };
}

const DEMO_RECORDS: DemoPaper[] = [
  {
    id: "10.1002_adfm.201705553",
    paperNumber: "PXRD-00001",
    doi: "10.1002/adfm.201705553",
    title: "Covalent Organic Frameworks: Structures, Synthesis, and Applications",
    authors: null,
    journal: "Advanced Functional Materials",
    year: 2018,
    sourceUrl: "https://doi.org/10.1002/adfm.201705553",
    figureCount: 1,
    curveCount: 5,
    materialCount: 4,
    figures: [
      {
        id: "10.1002_adfm.201705553-p015-f01",
        figureLabel: "Figure 7a-e",
        pageNumber: 15,
        caption:
          "Experimental brick-wall COF PXRD with simulated brick-wall and herringbone AA/AB packing models.",
        sourceCropUrl: "/demo/adfm-201705553-source.png",
        digitizedPlotUrl: "/demo/adfm-201705553-digitized.png",
        overlayUrl: null,
        qualityStatus: "pending",
        verificationStatus: "axis_cross_validated",
        axisAgreementDeg: 0.041,
        axisRmseDeg: 0.032,
        axisTickCount: 9,
        seriesDetected: 5,
        seriesDigitized: 5,
        seriesOmittedComputed: 0,
        curves: [
          curve("10.1002_adfm.201705553-p015-f01-s01", "Experimental", "experimental", "brick-wall COF", 1899, 2.015, 39.975, "/demo/adfm-201705553-curves.csv", { twoTheta: 4.813, fwhm: 0.44, uncertainty: 0.0461, confidence: 0.902, crystallineFraction: 0.731, hump: "hump_detected", humpCenter: 24.6, humpFwhm: 3.34, ratio: 6.12 }),
          curve("10.1002_adfm.201705553-p015-f01-s02", "Brick-wall-AB", "simulated", "Brick-wall-AB", 1902, 1.953, 39.973, "/demo/adfm-201705553-curves.csv"),
          curve("10.1002_adfm.201705553-p015-f01-s03", "Brick-wall-AA", "simulated", "Brick-wall-AA", 1905, 1.953, 40.033, "/demo/adfm-201705553-curves.csv"),
          curve("10.1002_adfm.201705553-p015-f01-s04", "Herringbone-AB", "simulated", "Herringbone-AB", 1902, 1.953, 39.973, "/demo/adfm-201705553-curves.csv"),
          curve("10.1002_adfm.201705553-p015-f01-s05", "Herringbone-AA", "simulated", "Herringbone-AA", 1902, 1.953, 39.973, "/demo/adfm-201705553-curves.csv"),
        ],
      },
    ],
  },
  {
    id: "10.1002_anie.202113657",
    paperNumber: "PXRD-00002",
    doi: "10.1002/anie.202113657",
    title: "An Expanded 2D Fused Aromatic Network with 90-Ring Hexagons",
    authors: "Alberto Riaño et al.",
    journal: "Angewandte Chemie International Edition",
    year: 2022,
    sourceUrl: "https://doi.org/10.1002/anie.202113657",
    figureCount: 1,
    curveCount: 4,
    materialCount: 1,
    figures: [
      {
        id: "10.1002_anie.202113657-p003-f01",
        figureLabel: "Figure 2f",
        pageNumber: 3,
        caption: "Experimental PXRD compared with AA, AB, and ABC stacking models.",
        sourceCropUrl: "/demo/anie-202113657-source.png",
        digitizedPlotUrl: "/demo/anie-202113657-digitized.png",
        overlayUrl: null,
        qualityStatus: "flagged",
        // The two axis fits disagreed across the plot. Every position on this
        // figure is provisional, and the small per-trace residuals below are
        // exactly the reassuring numbers that must not be shown unqualified.
        verificationStatus: "axis_arbitrated",
        axisAgreementDeg: 3.812,
        axisRmseDeg: 0.055,
        axisTickCount: 4,
        seriesDetected: 6,
        seriesDigitized: 5,
        seriesOmittedComputed: 1,
        curves: [
          curve("10.1002_anie.202113657-p003-f01-s01", "Experimental", "experimental", "KL-FAN", 2482, 0.24, 49.86, "/demo/anie-202113657-curves.csv", { twoTheta: 2.773, status: "low_confidence", fwhm: 0.30, uncertainty: 0.0836, confidence: 0.812, crystallineFraction: 0.607, hump: "hump_detected", humpCenter: 20.4, humpFwhm: 5.44, ratio: 31.9 }),
          curve("10.1002_anie.202113657-p003-f01-s02", "AA Stacking", "simulated", "KL-FAN", 2463, 0.94, 50.18, "/demo/anie-202113657-curves.csv"),
          curve("10.1002_anie.202113657-p003-f01-s03", "AB Stacking", "simulated", "KL-FAN", 2447, 0.94, 49.86, "/demo/anie-202113657-curves.csv"),
          curve("10.1002_anie.202113657-p003-f01-s04", "ABC Stacking", "simulated", "KL-FAN", 2452, 0.847, 49.867, "/demo/anie-202113657-curves.csv"),
        ],
      },
    ],
  },
  {
    id: "10.1021_acs.chemmater.3c01952",
    paperNumber: "PXRD-00003",
    doi: "10.1021/acs.chemmater.3c01952",
    title: "Controllable Switch of Thermodynamic and Kinetic Growing Paths in Two-Dimensional Covalent Organic Frameworks",
    authors: "Honghan Long et al.",
    journal: "Chemistry of Materials",
    year: 2024,
    sourceUrl: "https://doi.org/10.1021/acs.chemmater.3c01952",
    figureCount: 1,
    curveCount: 4,
    materialCount: 2,
    figures: [
      {
        id: "10.1021_acs.chemmater.3c01952-p003-f01",
        figureLabel: "Figure 2b",
        pageNumber: 3,
        caption: "COF-C2-80 experimental pattern with simulated AA, AB, and ABC stacking models.",
        sourceCropUrl: "/demo/chemmater-3c01952-source.png",
        digitizedPlotUrl: "/demo/chemmater-3c01952-digitized.png",
        overlayUrl: null,
        qualityStatus: "pending",
        verificationStatus: "axis_single_method",
        axisAgreementDeg: null,
        axisRmseDeg: 0.047,
        axisTickCount: 7,
        seriesDetected: 4,
        seriesDigitized: 4,
        seriesOmittedComputed: 0,
        curves: [
          curve("10.1021_acs.chemmater.3c01952-p003-f01-s01", "Experimental pattern", "experimental", "COF-C2-80", 1427, 1.483, 30.003, "/demo/chemmater-3c01952-curves.csv", { twoTheta: 3.383, fwhm: 0.41, uncertainty: 0.0459, confidence: 0.868, crystallineFraction: 0.797, hump: "no_hump_detected" }),
          curve("10.1021_acs.chemmater.3c01952-p003-f01-s02", "AA Stacking", "simulated", "COF-C2-80", 1427, 1.483, 30.003, "/demo/chemmater-3c01952-curves.csv", { twoTheta: 3.401, fwhm: 0.18, uncertainty: 0.0312, confidence: 0.941, crystallineFraction: 0.852, hump: "window_not_covered" }),
          curve("10.1021_acs.chemmater.3c01952-p003-f01-s03", "AB Stacking", "simulated", "COF-C2-80", 1427, 1.483, 30.003, "/demo/chemmater-3c01952-curves.csv", { status: "no_bragg_peak", uncertainty: 0.0374, confidence: 0.915 }),
          curve("10.1021_acs.chemmater.3c01952-p003-f01-s04", "KL-FAN reference", "reference", "KL-FAN", 1427, 1.483, 30.003, "/demo/chemmater-3c01952-curves.csv", { twoTheta: 3.39, fwhm: 0.46, uncertainty: 0.0503, confidence: 0.844, crystallineFraction: 0.688, hump: "hump_detected", humpCenter: 22.1, humpFwhm: 4.2, ratio: 12.4 }),
        ],
      },
    ],
  },
];

const DEMO_LABEL_INDEX = buildMaterialLabelIndex(
  DEMO_RECORDS.flatMap((paper) =>
    paper.figures.flatMap((figure) =>
      figure.curves.map((item) => ({ materialName: item.materialName, paperId: paper.id })),
    ),
  ),
);

export const DEMO_PAPERS: PaperDetail[] = DEMO_RECORDS.map((paper) => {
  const curves = paper.figures.flatMap((figure) => figure.curves);
  return {
    ...paper,
    materialNames: [...new Set(curves.flatMap((curve) => curve.materialName ? [curve.materialName] : []))],
    curveRoles: [...new Set(curves.map((curve) => curve.role))],
    sampleStates: [...new Set(curves.flatMap((curve) => curve.sampleState ? [curve.sampleState] : []))],
    qualityStatuses: [...new Set(paper.figures.map((figure) => figure.qualityStatus))],
    hasResolvedTitle: true,
    publicationStatus: "active" as const,
    publicationStatusNoticeDoi: null,
    sharedLabelKeys: DEMO_LABEL_INDEX.sharedByPaper.get(paper.id) ?? [],
  };
});

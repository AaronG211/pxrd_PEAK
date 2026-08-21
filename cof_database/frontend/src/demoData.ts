import type { PaperDetail, PxrdCurve } from "./types";

type DemoPaper = Omit<
  PaperDetail,
  "materialNames" | "curveRoles" | "sampleStates" | "qualityStatuses" | "hasResolvedTitle"
>;

function curve(
  seriesId: string,
  label: string,
  role: PxrdCurve["role"],
  materialName: string | null,
  pointCount: number,
  min: number,
  max: number,
  dataUrl: string,
): PxrdCurve {
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
        qualityStatus: "reviewed",
        curves: [
          curve("10.1002_adfm.201705553-p015-f01-s01", "Experimental", "experimental", "brick-wall COF", 1899, 2.015, 39.975, "/demo/adfm-201705553-curves.csv"),
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
        qualityStatus: "reviewed",
        curves: [
          curve("10.1002_anie.202113657-p003-f01-s01", "Experimental", "experimental", "KL-FAN", 2482, 0.24, 49.86, "/demo/anie-202113657-curves.csv"),
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
    materialCount: 1,
    figures: [
      {
        id: "10.1021_acs.chemmater.3c01952-p003-f01",
        figureLabel: "Figure 2b",
        pageNumber: 3,
        caption: "COF-C2-80 experimental pattern with simulated AA, AB, and ABC stacking models.",
        sourceCropUrl: "/demo/chemmater-3c01952-source.png",
        digitizedPlotUrl: "/demo/chemmater-3c01952-digitized.png",
        overlayUrl: null,
        qualityStatus: "reviewed",
        curves: [
          curve("10.1021_acs.chemmater.3c01952-p003-f01-s01", "Experimental pattern", "experimental", "COF-C2-80", 1427, 1.483, 30.003, "/demo/chemmater-3c01952-curves.csv"),
          curve("10.1021_acs.chemmater.3c01952-p003-f01-s02", "AA Stacking", "simulated", "COF-C2-80", 1427, 1.483, 30.003, "/demo/chemmater-3c01952-curves.csv"),
          curve("10.1021_acs.chemmater.3c01952-p003-f01-s03", "AB Stacking", "simulated", "COF-C2-80", 1427, 1.483, 30.003, "/demo/chemmater-3c01952-curves.csv"),
          curve("10.1021_acs.chemmater.3c01952-p003-f01-s04", "ABC Stacking", "simulated", "COF-C2-80", 1427, 1.483, 30.003, "/demo/chemmater-3c01952-curves.csv"),
        ],
      },
    ],
  },
];

export const DEMO_PAPERS: PaperDetail[] = DEMO_RECORDS.map((paper) => {
  const curves = paper.figures.flatMap((figure) => figure.curves);
  return {
    ...paper,
    materialNames: [...new Set(curves.flatMap((curve) => curve.materialName ? [curve.materialName] : []))],
    curveRoles: [...new Set(curves.map((curve) => curve.role))],
    sampleStates: [...new Set(curves.flatMap((curve) => curve.sampleState ? [curve.sampleState] : []))],
    qualityStatuses: [...new Set(paper.figures.map((figure) => figure.qualityStatus))],
    hasResolvedTitle: true,
  };
});

import type { CurveAdmission } from "../types";

/**
 * How each non-clean admission class is named and explained to a reader.
 *
 * The site publishes every curve the paper releases and marks the ones outside
 * the clean set instead of withholding them (see CurveAdmission in ../types).
 * The paper page marks each such curve and explains the mark in one line; the
 * browse page names the same classes when it says what "outside the clean set"
 * is made of. One table, so the two pages cannot describe a class differently.
 */
export type NonCleanAdmission = Exclude<CurveAdmission, "clean">;

/** Display order: the two sibling-flag classes, then the curve's own problems. */
export const NON_CLEAN_ADMISSIONS: readonly NonCleanAdmission[] = [
  "axis_verified",
  "axis_unconfirmed",
  "shape_flagged",
  "model_derived",
];

export const ADMISSION_MARKS: Record<
  NonCleanAdmission,
  { mark: string; explanation: string; tone: string }
> = {
  // Deliberately understated: every re-admitted curve sits in a cross-validated
  // figure, against 82.6% of clean ones. The mark exists so the distinction is
  // never hidden, not to imply the data is worse.
  axis_verified: {
    mark: "re-admitted",
    explanation:
      "Another trace in this figure failed the automated shape checks; this one passed on its own, and the figure’s 2θ axis was confirmed by two independent fits.",
    tone: "text-slate-400",
  },
  axis_unconfirmed: {
    mark: "axis unconfirmed",
    explanation:
      "This trace passed the automated shape checks, but another trace in the figure did not, and the figure’s 2θ axis was not confirmed by two independent fits.",
    tone: "text-amber-600",
  },
  shape_flagged: {
    mark: "shape-flagged",
    explanation:
      "This trace itself failed an automated shape check (a staircase or notch in the line, or a trace that strayed off the printed curve); compare it with the source figure before relying on it.",
    tone: "text-amber-700",
  },
  model_derived: {
    mark: "model-derived",
    explanation:
      "A label check after extraction found this is a simulated, refined or difference curve, not a measured pattern.",
    tone: "text-sky-700",
  },
};

/** The mark for a class, or null for `clean` and for any value this build does not know. */
export function admissionMark(admission: CurveAdmission | string) {
  return Object.prototype.hasOwnProperty.call(ADMISSION_MARKS, admission)
    ? ADMISSION_MARKS[admission as NonCleanAdmission]
    : null;
}

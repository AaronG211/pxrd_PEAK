import type { CurveRole } from "../types";

/**
 * How a curve's `role` is shown to a reader.
 *
 * `unclassified` is the fall-through of the importer's role rule
 * (infer_role in cof_database/scripts/import_pxrd_to_supabase.py): a role from
 * the per-curve contexts table if there is one, else "experimental" when the
 * recorded sample state says so, else a keyword in the legend label
 * ("simulated", "Pawley", "difference", "JCPDS"…), else `unclassified`. The
 * contexts table covers 122 curves in 20 papers, so for nearly every curve the
 * rule had only the label and the sample state to go on.
 *
 * So `unclassified` means "no role recorded", NOT "the figure did not say":
 * legends reading "ycal", "NH2-MIL-125-Sim" or "Experimental (background
 * corrected)" land here, as do 120 of the 204 curves the label check marked
 * model_derived. It is common, too: 6,028 of the 11,445 published curves
 * (outputs/pxrd.db, 2026-10-24). Printed as a chip on all of those, the word
 * read as a classification failure on curves that are fine, and it repeated
 * down whole inventories.
 *
 * So the UI shows a role only where one was recorded, and the value itself is
 * untouched: the API and the bulk files still say `unclassified`, and so does
 * the browse facet's option value. Where the absence has to be named, the
 * wording is UNRECORDED_ROLE_LABEL, which claims nothing about the source.
 */
export type ExplicitCurveRole = Exclude<CurveRole, "unclassified">;

const EXPLICIT_ROLES: ReadonlySet<string> = new Set<ExplicitCurveRole>([
  "experimental",
  "simulated",
  "refined",
  "difference",
  "reference",
]);

/**
 * True when a role was recorded for this trace.
 *
 * Membership rather than `!== "unclassified"`, so a value this build does not
 * know is treated as unrecorded instead of indexing a style table with it.
 */
export function isExplicitRole(role: CurveRole | string | null | undefined): role is ExplicitCurveRole {
  return typeof role === "string" && EXPLICIT_ROLES.has(role);
}

/** The reader-facing name for `unclassified`, wherever one is needed at all. */
export const UNRECORDED_ROLE_LABEL = "Role not recorded";

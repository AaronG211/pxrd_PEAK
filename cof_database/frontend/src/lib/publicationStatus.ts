import type { PublicationStatus } from "../types";

/**
 * Publisher lifecycle disclosure, derived from Crossref Crossmark `updated-by`
 * links and, where the publisher has deposited none, the `RETRACTED:` title
 * stamp. Records are always shown and marked, never hidden — dropping the DOI
 * would break stable links and conceal the very thing worth disclosing.
 */
export const PUBLICATION_STATUS_LABEL: Record<Exclude<PublicationStatus, "active">, string> = {
  retracted: "Retracted",
  withdrawn: "Withdrawn",
  concern: "Expression of concern",
  corrected: "Corrected",
};

/** rose = the site's danger token, amber = its caution token. */
export const PUBLICATION_STATUS_CLASS: Record<Exclude<PublicationStatus, "active">, string> = {
  retracted: "border-rose-200 bg-rose-50 text-rose-700",
  withdrawn: "border-rose-200 bg-rose-50 text-rose-700",
  concern: "border-amber-200 bg-amber-50 text-amber-700",
  corrected: "border-amber-200 bg-amber-50 text-amber-700",
};

export function publicationStatusLabel(status: PublicationStatus): string {
  return status === "active" ? "" : PUBLICATION_STATUS_LABEL[status];
}

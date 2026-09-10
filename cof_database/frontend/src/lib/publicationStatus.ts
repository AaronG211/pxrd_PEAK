import type { PublicationStatus } from "../types";

/**
 * Publisher lifecycle disclosure, derived from Crossref Crossmark `updated-by`
 * links and, where the publisher has deposited none, the `RETRACTED:` title
 * stamp. Records are always shown and marked, never hidden — dropping the DOI
 * would break stable links and conceal the very thing worth disclosing.
 */
export const PUBLICATION_STATUS_LABEL: Record<Exclude<PublicationStatus, "active">, string> = {
  unchecked: "Publication status not checked",
  retracted: "Retracted",
  withdrawn: "Withdrawn",
  concern: "Expression of concern",
  corrected: "Corrected",
};

/** rose = danger, amber = caution, slate = absence of information. */
export const PUBLICATION_STATUS_CLASS: Record<Exclude<PublicationStatus, "active">, string> = {
  unchecked: "border-slate-200 bg-slate-50 text-slate-600",
  retracted: "border-rose-200 bg-rose-50 text-rose-700",
  withdrawn: "border-rose-200 bg-rose-50 text-rose-700",
  concern: "border-amber-200 bg-amber-50 text-amber-700",
  corrected: "border-amber-200 bg-amber-50 text-amber-700",
};

export function publicationStatusLabel(status: PublicationStatus): string {
  return status === "active" ? "" : PUBLICATION_STATUS_LABEL[status];
}

/**
 * Whether the status is a publisher notice worth interrupting a reader for.
 *
 * `unchecked` is excluded because it is not a publisher notice. It is the
 * absence of a check on our side, and this predicate decides whether to
 * interrupt a reader on a publisher's behalf.
 *
 * That reason replaces a weaker one. The comment here used to say `unchecked`
 * sat on every one of the 1,000 live papers, so badging it would be noise. The
 * Crossref backfill has since resolved all of them — the live collection is
 * 2,226 active, 10 corrected, 2 retracted, and ZERO unchecked — so the old
 * rationale now argues for nothing. The behaviour was right for the other
 * reason all along, and it still holds if unchecked papers ever return.
 *
 * It stays disclosed on the paper detail page, where there is room to say what
 * it means, rather than hidden outright.
 */
export function isPublisherNotice(status: PublicationStatus): boolean {
  return status !== "active" && status !== "unchecked";
}

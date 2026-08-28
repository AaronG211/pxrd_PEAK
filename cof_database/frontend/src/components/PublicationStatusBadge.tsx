import { CircleAlert, TriangleAlert } from "lucide-react";
import { PUBLICATION_STATUS_CLASS, PUBLICATION_STATUS_LABEL } from "../lib/publicationStatus";
import type { PublicationStatus } from "../types";

export function PublicationStatusBadge({ status }: { status: PublicationStatus }) {
  if (status === "active") return null;
  const severe = status === "retracted" || status === "withdrawn";
  return (
    <span className={`badge ${PUBLICATION_STATUS_CLASS[status]}`}>
      {severe ? (
        <TriangleAlert className="mr-1 h-3 w-3" aria-hidden="true" />
      ) : (
        <CircleAlert className="mr-1 h-3 w-3" aria-hidden="true" />
      )}
      {PUBLICATION_STATUS_LABEL[status]}
    </span>
  );
}

export function PublicationStatusBanner({
  status,
  noticeDoi,
}: {
  status: PublicationStatus;
  noticeDoi: string | null;
}) {
  // Only a retraction or withdrawal warrants a full-width alarm; a corrigendum
  // is disclosed by the chip alone.
  if (status !== "retracted" && status !== "withdrawn") return null;
  const verb = status === "retracted" ? "retracted" : "withdrawn";
  return (
    <div
      role="alert"
      className="mb-5 flex items-start gap-3 rounded-xl border border-rose-200 bg-rose-50 p-4 text-sm text-rose-800"
    >
      <TriangleAlert className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
      <div>
        <p className="font-semibold">This paper has been {verb} by the publisher.</p>
        <p className="mt-1">
          The digitized patterns below are preserved for the record and should not be used
          as reference data.
          {noticeDoi && (
            <>
              {" "}
              <a
                href={`https://doi.org/${noticeDoi}`}
                target="_blank"
                rel="noreferrer"
                className="font-mono font-semibold underline"
              >
                Notice: {noticeDoi}
              </a>
            </>
          )}
        </p>
      </div>
    </div>
  );
}

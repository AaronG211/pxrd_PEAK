import { useEffect } from "react";

const SITE = "Open PXRD Database";

/**
 * Set the document title for the current route.
 *
 * The SPA shipped a single static <title> from index.html, so all 1,000 paper
 * pages, the index, the planner and the auth callback announced themselves
 * identically. That is WCAG 2.4.2 (Level A) — the title is the first thing a
 * screen reader speaks on navigation, and it is what labels tabs, history
 * entries and bookmarks. A researcher with ten paper tabs open could not tell
 * them apart.
 *
 * Pass null while the page is still resolving to leave the previous title in
 * place rather than flashing a placeholder.
 */
export function useDocumentTitle(title: string | null): void {
  useEffect(() => {
    if (title === null) return;
    const previous = document.title;
    document.title = title === SITE ? SITE : `${title} · ${SITE}`;
    return () => {
      document.title = previous;
    };
  }, [title]);
}

export const SITE_TITLE = SITE;

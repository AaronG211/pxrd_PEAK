import { useEffect } from "react";

const SITE = "Open PXRD Database";

export type PageMeta = {
  /** Page-specific part of the title. Pass null while the record resolves. */
  title: string | null;
  description?: string;
  /** Path only, e.g. "/paper/10.1021_x". Origin is filled in. */
  canonical?: string;
  /** Keep this page out of the index (soft 404s, auth-gated tools). */
  noIndex?: boolean;
  /** schema.org object, serialised into a JSON-LD script tag. */
  jsonLd?: Record<string, unknown> | null;
};

function upsertMeta(selector: string, create: () => HTMLElement, apply: (el: HTMLElement) => void) {
  let el = document.head.querySelector<HTMLElement>(selector);
  let created = false;
  if (!el) {
    el = create();
    document.head.appendChild(el);
    created = true;
  }
  const previous = el.cloneNode(true) as HTMLElement;
  apply(el);
  return () => {
    if (!el) return;
    if (created) el.remove();
    else el.replaceWith(previous);
  };
}

/**
 * Per-route document metadata for a client-rendered SPA.
 *
 * The app shipped one static <title> and one description for all 2,000+ routes,
 * no canonical, no structured data, and returned HTTP 200 for paths that do not
 * exist. Measured against the live site, a search for its own hostname returned
 * nothing at all.
 *
 * Everything here is set at runtime, which Google's rendering pass does read but
 * which Google Scholar largely does not. Scholar wants citation_* tags in the
 * served HTML, so the durable fix for that specific audience is prerendering the
 * paper routes at build time; this hook is what makes that a swap of where the
 * tags come from rather than a rewrite.
 */
export function usePageMeta({ title, description, canonical, noIndex, jsonLd }: PageMeta): void {
  useEffect(() => {
    if (title === null) return;
    const previous = document.title;
    document.title = title === SITE ? SITE : `${title} · ${SITE}`;
    return () => {
      document.title = previous;
    };
  }, [title]);

  useEffect(() => {
    if (!description) return;
    return upsertMeta(
      'meta[name="description"]',
      () => {
        const el = document.createElement("meta");
        el.setAttribute("name", "description");
        return el;
      },
      (el) => el.setAttribute("content", description),
    );
  }, [description]);

  useEffect(() => {
    if (!canonical) return;
    const href = new URL(canonical, window.location.origin).toString();
    return upsertMeta(
      'link[rel="canonical"]',
      () => {
        const el = document.createElement("link");
        el.setAttribute("rel", "canonical");
        return el;
      },
      (el) => el.setAttribute("href", href),
    );
  }, [canonical]);

  useEffect(() => {
    if (!noIndex) return;
    return upsertMeta(
      'meta[name="robots"]',
      () => {
        const el = document.createElement("meta");
        el.setAttribute("name", "robots");
        return el;
      },
      (el) => el.setAttribute("content", "noindex, follow"),
    );
  }, [noIndex]);

  useEffect(() => {
    if (!jsonLd) return;
    const script = document.createElement("script");
    script.type = "application/ld+json";
    script.dataset.pageMeta = "true";
    script.textContent = JSON.stringify(jsonLd);
    document.head.appendChild(script);
    return () => script.remove();
  }, [jsonLd]);
}

export const SITE_TITLE = SITE;

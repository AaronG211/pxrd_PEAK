"""OpenAI integrations used for figure classification and metadata extraction."""

from __future__ import annotations

import hashlib
import json
import random
import time
from pathlib import Path
from typing import Optional, Sequence, Type, TypeVar

from pydantic import BaseModel

from .config import Settings
from .schemas import (
    ArtifactAssistance,
    FigureCurveAnalysis,
    LegendAssignment,
    MetadataExtraction,
    PagePxrdScreening,
    PxrdClassification,
    ReplotVerification,
)
from .utils import encode_image_to_data_url

SchemaT = TypeVar("SchemaT", bound=BaseModel)

_CACHE_ROOT = Path(".cache") / "openai"
_RETRY_MAX_ATTEMPTS = 4
_RETRY_BASE_DELAY_SECONDS = 1.5
_TRANSIENT_ERROR_HINTS = (
    "429",
    "500",
    "502",
    "503",
    "504",
    "timeout",
    "timed out",
    "temporarily",
    "rate limit",
    "connection",
    "econnreset",
)

ONE_PIXEL_PNG_DATA_URL = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Wv8WlIAAAAASUVORK5CYII="
)


class OpenAIServiceError(RuntimeError):
    """Raised when the OpenAI integration cannot fulfill a request."""


def _is_transient_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return any(hint in message for hint in _TRANSIENT_ERROR_HINTS)


class OpenAIService:
    """Small wrapper around the OpenAI Python SDK."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.cache_enabled = True
        try:
            from openai import OpenAI
        except ImportError as exc:  # pragma: no cover - import guard
            raise OpenAIServiceError("The `openai` package is not installed.") from exc

        self.client = OpenAI(
            api_key=settings.chatgpt_api_key.get_secret_value(),
            timeout=settings.openai_timeout_seconds,
        )

    def probe_model(self) -> str:
        """Validate that the configured model supports multimodal structured output."""

        class ProbePayload(BaseModel):
            ok: bool

        response = self._parse_response(
            ProbePayload,
            system_prompt=(
                "You are validating API capabilities. Return {\"ok\": true} when you can "
                "read a tiny image and follow structured output instructions."
            ),
            user_text="Respond with ok=true only.",
            image_data_url=ONE_PIXEL_PNG_DATA_URL,
        )
        if not response.ok:
            raise OpenAIServiceError(
                "The configured model responded, but did not satisfy the structured-output probe."
            )
        return "Structured output + image input probe succeeded."

    def classify_pxrd(
        self,
        image_path: Path,
        caption: str,
        context: str,
        keyword_score: float,
    ) -> PxrdClassification:
        prompt = (
            "Classify whether this scientific figure crop contains a powder X-ray diffraction "
            "pattern. Use the caption, context, and visible chart style. "
            "Return low confidence when the image is ambiguous.\n\n"
            f"Caption: {caption or 'N/A'}\n"
            f"Context: {context or 'N/A'}\n"
            f"Local keyword score: {keyword_score:.3f}"
        )
        return self._parse_response(
            PxrdClassification,
            system_prompt=(
                "You classify figure crops from chemistry papers. "
                "Return strict JSON matching the schema. "
                "CRITICAL: Look closely at the X-axis of the image. PXRD graphs usually show "
                "'2θ', '2 Theta', '2θ (deg)', '2θ/°', 'Bragg angle', or similar variations. "
                "Plots with 'Relative Pressure', 'Quantity Adsorbed', 'Wavenumber', 'Wavelength', "
                "or 'Temperature' on the X-axis are NOT PXRD and should be marked is_pxrd=false."
            ),
            user_text=prompt,
            image_data_url=encode_image_to_data_url(image_path),
        )

    def screen_pxrd_page(
        self,
        page_image_path: Path,
        page_text: str,
        page_number: int,
    ) -> PagePxrdScreening:
        prompt = (
            "Decide whether this PDF page contains at least one PXRD/XRD diffractogram that "
            "should be kept for downstream extraction. Return false for text-only pages, tables, "
            "SEM/TEM images, spectroscopy, and generic plots that are not PXRD. "
            "Visible x-axis labels like 2θ, 2 theta, degree, or stacked diffraction traces are "
            "strong positive cues.\n\n"
            f"Page number: {page_number}\n"
            f"Extracted page text (truncated): {(page_text or 'N/A')[:3500]}"
        )
        return self._parse_response(
            PagePxrdScreening,
            system_prompt=(
                "You screen scientific-paper pages for PXRD graphs. "
                "Return strict JSON matching the schema. "
                "Be inclusive: if a page visibly contains a PXRD/XRD graph, even if it is small, "
                "part of a multi-panel figure, or in the corner, you MUST keep it (has_pxrd=true). "
                "Only reject pages that are clearly just text, tables, or non-PXRD imagery (like SEM/TEM)."
            ),
            user_text=prompt,
            image_data_url=encode_image_to_data_url(page_image_path),
        )

    def assist_non_curve_regions(
        self,
        *,
        image_data_url: str,
        image_width: int,
        image_height: int,
        overlay_similarity: float,
        flagged_reasons: Sequence[str],
        existing_box_count: int,
        artifact_fraction: float,
    ) -> ArtifactAssistance:
        prompt = (
            "Identify non-curve regions inside this scientific plot crop. The image already contains "
            "only the interior plot box. The main diffraction curve or curves must never be boxed.\n\n"
            "Return coarse but useful bounding boxes in the crop pixel coordinate system.\n"
            "Image width: {width}\n"
            "Image height: {height}\n"
            "Current overlay similarity: {overlay:.3f}\n"
            "Current CV artifact box count: {box_count}\n"
            "Current CV artifact fraction: {artifact_fraction:.4f}\n"
            "Current flags: {flags}\n\n"
            "Include only non-curve objects such as axis/frame lines, tick labels, axis titles, "
            "peak labels, legends, inset graphics, and stray text. Prefer a small number of merged "
            "regions over many tiny boxes. If there is nothing useful to add, return an empty list."
        ).format(
            width=image_width,
            height=image_height,
            overlay=overlay_similarity,
            box_count=existing_box_count,
            artifact_fraction=artifact_fraction,
            flags=", ".join(flagged_reasons) or "none",
        )
        return self._parse_response(
            ArtifactAssistance,
            system_prompt=(
                "You locate non-curve artifacts inside scientific plot crops. "
                "Return strict JSON matching the schema. "
                "All bounding boxes must use integer pixel coordinates relative to the provided crop. "
                "Do not include the main plotted PXRD curve in any region. "
                "Use only these kinds when relevant: axis_frame, tick_label, axis_title, "
                "peak_label, legend, inset, text, other."
            ),
            user_text=prompt,
            image_data_url=image_data_url,
        )

    def extract_metadata(
        self,
        caption: str,
        context: str,
        figure_label: Optional[str],
    ) -> MetadataExtraction:
        prompt = (
            "Extract concise PXRD-related metadata from the supplied figure caption and nearby "
            "text. If a field is missing, leave it null or empty.\n\n"
            f"Figure label hint: {figure_label or 'N/A'}\n"
            f"Caption: {caption or 'N/A'}\n"
            f"Context: {context or 'N/A'}"
        )
        return self._parse_response(
            MetadataExtraction,
            system_prompt=(
                "You extract structured metadata from scientific-figure captions and nearby text. "
                "Prefer exact names when present. Do not hallucinate missing peaks."
            ),
            user_text=prompt,
        )

    def analyze_pxrd_curves(
        self,
        image_path: Path,
        caption: str,
        context: str,
        figure_label: Optional[str],
        likely_series_count: int,
    ) -> FigureCurveAnalysis:
        prompt = (
            "Estimate how many distinct PXRD/XRD curves appear in this figure crop. "
            "Count only real diffraction traces inside the plot box. Do not count axes, "
            "legends, insets, or text annotations as curves.\n\n"
            "IMPORTANT EXCLUSIONS — do NOT count the following as curves:\n"
            "  - Bragg position markers: rows of short vertical tick marks at the bottom of the plot "
            "(these are discrete position indicators, not continuous data traces).\n"
            "  - Simulated / calculated / reference patterns: very thin lines (1–2 px) that sit "
            "near the bottom baseline of the plot with almost no vertical variation — these are "
            "computed reference patterns, not experimental data. Exclude them from the curve list.\n"
            "  - Stick patterns or bar charts (zero-width vertical lines).\n\n"
            "If possible, use the caption, nearby context, visible legend text, inline labels, "
            "and vertical stacking order to infer what each curve represents. "
            "Return curve descriptors sorted from top to bottom in the image.\n\n"
            "CRITICAL: For each curve, look carefully at any text labels printed directly on the plot "
            "(inline labels next to the curve, legend entries, or axis annotations). "
            "Extract the FULL chemical or material name exactly as written — for example "
            "'TpPa-SO3H_experimental', 'TpDBD-Phos', 'ZIF-8 as-synthesized' — and assign it to "
            "the `label` field.  Also populate `material_name` with the compound/material part and "
            "`sample_name` with any sample-state qualifier (e.g. 'experimental', 'simulated', "
            "'calcined').  Do NOT use generic words like 'curve 1' or 'experimental' alone as the "
            "label; always include the compound name if it is visible anywhere in the figure.\n\n"
            "CRITICAL: For each curve, identify its visual color in the plot (e.g., 'red', 'blue', 'black', 'green', 'orange', 'cyan', 'magenta', 'gray'). "
            "If the curve is mostly black or dark gray, use 'black'. Assign this to the `visible_color` field.\n\n"
            f"Figure label hint: {figure_label or 'N/A'}\n"
            f"Caption: {caption or 'N/A'}\n"
            f"Context: {context or 'N/A'}\n"
            f"Existing likely series count hint: {likely_series_count}"
        )
        return self._parse_response(
            FigureCurveAnalysis,
            system_prompt=(
                "You analyze PXRD figure crops from scientific papers. "
                "Return strict JSON matching the schema. "
                "Always extract the full chemical/material name from visible plot text for the `label` field. "
                "Only leave label null when no name is visible anywhere in the figure. "
                "The curves array must be ordered from top to bottom. "
                "Always populate `visible_color` with a simple English color name."
            ),
            user_text=prompt,
            image_data_url=encode_image_to_data_url(image_path),
        )

    def verify_replot(
        self,
        *,
        crop_image_data_url: str,
        replot_image_data_url: str,
        crop_width: int,
        crop_height: int,
        series_label: Optional[str],
        processed_peaks_summary: str,
        caption: str,
    ) -> ReplotVerification:
        prompt = (
            "Two images are supplied: (1) the ORIGINAL PXRD figure crop, and "
            "(2) our pipeline's RE-PLOTTED digitized curve for one series. "
            "Decide whether the re-plot faithfully reproduces the original "
            "trace for this series. Focus on the SHAPE of peaks (positions "
            "on the 2θ axis, relative heights, widths), the baseline behavior, "
            "and whether any peaks were missed or spuriously added.\n\n"
            "If the replot deviates, classify the MOST LIKELY root cause:\n"
            "  - calibration: 2θ axis is stretched, shifted, or inverted; peaks land at wrong degrees.\n"
            "  - tracking: the traced curve ran along noise, a legend line, or the wrong series.\n"
            "  - baseline: a broad hump was not removed or over-subtracted; baseline slope is wrong.\n"
            "  - peak_miss: real peaks are absent in the replot, or replot has peaks that are not in the original.\n"
            "  - wrong_curve: the pipeline tracked a different series than what this replot claims to represent.\n"
            "  - other: any other mismatch.\n\n"
            "`disagreement_regions` must use integer pixel coordinates relative to the ORIGINAL crop "
            "({width} x {height}). Include only regions where the shapes clearly disagree.\n"
            "`fix_hint` should be a short actionable English hint (e.g., "
            "'stiffen baseline', 'retry with red series', 'lower peak-prominence threshold').\n\n"
            "Series label hint: {label}\n"
            "Detected peaks summary: {peaks}\n"
            "Caption: {caption}"
        ).format(
            width=crop_width,
            height=crop_height,
            label=series_label or "N/A",
            peaks=processed_peaks_summary or "N/A",
            caption=caption or "N/A",
        )
        return self._parse_response_multi(
            ReplotVerification,
            system_prompt=(
                "You verify whether a digitized PXRD re-plot faithfully reproduces "
                "the original figure for a single series. Return strict JSON matching "
                "the schema. Be decisive: set faithful=true only when peak positions "
                "AND relative heights are clearly preserved. When faithful=false, you "
                "MUST populate likely_cause and fix_hint."
            ),
            user_text=prompt,
            image_data_urls=[crop_image_data_url, replot_image_data_url],
        )

    def assign_legend_to_curves(
        self,
        image_path: Path,
        *,
        current_curves: Sequence[dict],
        caption: str,
        context: str,
    ) -> LegendAssignment:
        prompt = (
            "Inspect the figure for ALL printed labels that identify individual PXRD curves: "
            "the legend box, inline labels near traces, and any sample names adjacent to a trace. "
            "For each printed label, assign it to the single curve it refers to. "
            "The curves array below lists what our pipeline already tracked, ordered top-to-bottom.\n\n"
            "Rules:\n"
            "  - `assigned_curve_index` is the 0-based index into the curves array. "
            "Use -1 when the label exists but does not clearly belong to any tracked curve.\n"
            "  - `visible_color` should be a simple English color name for the trace that label refers to.\n"
            "  - Use the label text EXACTLY as printed (including subscripts, dashes, and state qualifiers).\n"
            "  - Prefer legend entries over inline labels when both exist for the same trace.\n"
            "  - Do NOT include axis titles, tick labels, hkl annotations like '(100)', or figure captions.\n\n"
            "Currently tracked curves (order_from_top | color_hint | label_hint):\n"
            + "\n".join(
                "  [{idx}] order={order} color={color} label={label}".format(
                    idx=index,
                    order=entry.get("order_from_top", index + 1),
                    color=entry.get("visible_color") or "unknown",
                    label=entry.get("label") or entry.get("series_label") or "n/a",
                )
                for index, entry in enumerate(current_curves)
            )
            + "\n\nCaption: {caption}\nContext: {context}".format(
                caption=caption or "N/A",
                context=(context or "N/A")[:2000],
            )
        )
        return self._parse_response(
            LegendAssignment,
            system_prompt=(
                "You match printed legend/inline labels to tracked PXRD curves in a scientific figure. "
                "Return strict JSON matching the schema. "
                "The curves array in the user message is authoritative: every `assigned_curve_index` "
                "must be -1 or a valid 0-based index into it. "
                "Preserve the exact printed text of each label."
            ),
            user_text=prompt,
            image_data_url=encode_image_to_data_url(image_path),
        )

    def _parse_response_multi(
        self,
        schema: Type[SchemaT],
        *,
        system_prompt: str,
        user_text: str,
        image_data_urls: Sequence[str],
    ) -> SchemaT:
        return self._parse_response(
            schema,
            system_prompt=system_prompt,
            user_text=user_text,
            image_data_urls=list(image_data_urls),
        )

    def _parse_response(
        self,
        schema: Type[SchemaT],
        *,
        system_prompt: str,
        user_text: str,
        image_data_url: Optional[str] = None,
        image_data_urls: Optional[Sequence[str]] = None,
    ) -> SchemaT:
        urls: list = []
        if image_data_url:
            urls.append(image_data_url)
        if image_data_urls:
            urls.extend(image_data_urls)

        content = [{"type": "input_text", "text": user_text}]
        for url in urls:
            content.append({"type": "input_image", "image_url": url})

        cache_enabled = getattr(self, "cache_enabled", False)
        cache_path: Optional[Path] = None
        if cache_enabled:
            cache_path = self._cache_path(
                schema=schema,
                system_prompt=system_prompt,
                user_text=user_text,
                image_data_urls=urls,
            )
            cached = self._load_cached_response(cache_path, schema)
            if cached is not None:
                return cached

        response = self._call_with_retry(
            system_prompt=system_prompt,
            content=content,
            schema=schema,
        )

        parsed = getattr(response, "output_parsed", None)
        if parsed is not None:
            if cache_path is not None:
                self._store_cached_response(cache_path, parsed)
            return parsed

        refusal = self._extract_refusal(response)
        if refusal:
            raise OpenAIServiceError(refusal)

        raw_text = getattr(response, "output_text", None) or ""
        if not raw_text:
            raise OpenAIServiceError("OpenAI returned no structured content.")

        validated = schema.model_validate(json.loads(raw_text))
        if cache_path is not None:
            self._store_cached_response(cache_path, validated)
        return validated

    def _call_with_retry(
        self,
        *,
        system_prompt: str,
        content: list,
        schema: Type[SchemaT],
    ) -> object:
        last_exc: Optional[Exception] = None
        for attempt in range(_RETRY_MAX_ATTEMPTS):
            try:
                return self.client.responses.parse(
                    model=self.settings.chatgpt_model,
                    input=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": content},
                    ],
                    text_format=schema,
                )
            except Exception as exc:  # pragma: no cover - SDK behavior
                last_exc = exc
                if not _is_transient_error(exc) or attempt == _RETRY_MAX_ATTEMPTS - 1:
                    raise OpenAIServiceError(str(exc)) from exc
                delay = _RETRY_BASE_DELAY_SECONDS * (2 ** attempt)
                delay += random.uniform(0.0, delay * 0.25)
                time.sleep(delay)
        # Unreachable — the loop either returns or raises.
        raise OpenAIServiceError(str(last_exc) if last_exc else "Unknown OpenAI error")

    @staticmethod
    def _cache_path(
        *,
        schema: Type[BaseModel],
        system_prompt: str,
        user_text: str,
        image_data_urls: Sequence[str] = (),
    ) -> Path:
        digest = hashlib.sha256()
        digest.update(schema.__name__.encode("utf-8"))
        digest.update(b"\0")
        digest.update(system_prompt.encode("utf-8"))
        digest.update(b"\0")
        digest.update(user_text.encode("utf-8"))
        for url in image_data_urls:
            digest.update(b"\0")
            digest.update(url.encode("utf-8"))
        key = digest.hexdigest()
        return _CACHE_ROOT / schema.__name__ / "{key}.json".format(key=key)

    @staticmethod
    def _load_cached_response(
        cache_path: Path,
        schema: Type[SchemaT],
    ) -> Optional[SchemaT]:
        if not cache_path.exists():
            return None
        try:
            payload = json.loads(cache_path.read_text())
            return schema.model_validate(payload)
        except Exception:
            return None

    @staticmethod
    def _store_cached_response(cache_path: Path, value: BaseModel) -> None:
        try:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(value.model_dump_json())
        except Exception:
            # Cache is advisory — never block a successful response on IO errors.
            pass

    @staticmethod
    def _extract_refusal(response: object) -> Optional[str]:
        output = getattr(response, "output", None)
        if not output:
            return None
        for item in output:
            for content_item in getattr(item, "content", []):
                refusal = getattr(content_item, "refusal", None)
                if refusal:
                    return str(refusal)
        return None

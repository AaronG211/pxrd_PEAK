import json
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from pxrd_fetcher.openai_service import OpenAIService, OpenAIServiceError
from pxrd_fetcher.schemas import (
    ArtifactAssistance,
    ArtifactKind,
    ArtifactRegion,
    CurveDescriptor,
    FigureCurveAnalysis,
)


class DemoSchema(BaseModel):
    ok: bool
    label: str = "demo"


class FakeResponses:
    def __init__(self, response):
        self._response = response

    def parse(self, **kwargs):
        return self._response


def make_service(response):
    service = object.__new__(OpenAIService)
    service.settings = SimpleNamespace(chatgpt_model="gpt-5.4-2026-03-05")
    service.client = SimpleNamespace(responses=FakeResponses(response))
    return service


def test_parse_response_uses_output_parsed():
    response = SimpleNamespace(output_parsed=DemoSchema(ok=True), output=[])
    service = make_service(response)

    parsed = service._parse_response(
        DemoSchema,
        system_prompt="system",
        user_text="user",
    )

    assert parsed.ok is True


def test_parse_response_falls_back_to_output_text():
    response = SimpleNamespace(output_parsed=None, output=[], output_text=json.dumps({"ok": True, "label": "x"}))
    service = make_service(response)

    parsed = service._parse_response(
        DemoSchema,
        system_prompt="system",
        user_text="user",
    )

    assert parsed.label == "x"


def test_parse_response_raises_on_refusal():
    refusal_item = SimpleNamespace(content=[SimpleNamespace(refusal="refused")])
    response = SimpleNamespace(output_parsed=None, output=[refusal_item], output_text="")
    service = make_service(response)

    with pytest.raises(OpenAIServiceError):
        service._parse_response(
            DemoSchema,
            system_prompt="system",
            user_text="user",
        )


def test_assist_non_curve_regions_returns_structured_regions():
    response = SimpleNamespace(
        output_parsed=ArtifactAssistance(
            regions=[
                ArtifactRegion(
                    kind=ArtifactKind.PEAK_LABEL,
                    confidence=0.93,
                    bbox=[12, 10, 38, 28],
                    note="(100)",
                )
            ],
            summary="peak annotation",
        ),
        output=[],
    )
    service = make_service(response)

    parsed = service.assist_non_curve_regions(
        image_data_url="data:image/png;base64,abc",
        image_width=240,
        image_height=160,
        overlay_similarity=0.61,
        flagged_reasons=["Weak curve coverage"],
        existing_box_count=2,
        artifact_fraction=0.18,
    )

    assert parsed.summary == "peak annotation"
    assert len(parsed.regions) == 1
    assert parsed.regions[0].kind == ArtifactKind.PEAK_LABEL
    assert parsed.regions[0].bbox == [12, 10, 38, 28]


def test_analyze_pxrd_curves_returns_curve_labels(tmp_path):
    image_path = tmp_path / "curve.png"
    image_path.write_bytes(b"fake")
    response = SimpleNamespace(
        output_parsed=FigureCurveAnalysis(
            curve_count=2,
            confidence=0.87,
            reason="two stacked traces",
            curves=[
                CurveDescriptor(order_from_top=1, label="pristine", confidence=0.91),
                CurveDescriptor(order_from_top=2, label="cycled", confidence=0.86),
            ],
        ),
        output=[],
    )
    service = make_service(response)

    parsed = service.analyze_pxrd_curves(
        image_path,
        caption="PXRD patterns of pristine and cycled samples.",
        context="The upper trace is pristine and the lower trace is cycled.",
        figure_label="Figure 3a",
        likely_series_count=2,
    )

    assert parsed.curve_count == 2
    assert [curve.label for curve in parsed.curves] == ["pristine", "cycled"]

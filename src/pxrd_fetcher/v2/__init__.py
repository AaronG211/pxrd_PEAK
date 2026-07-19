"""Fully-automatic PXRD extraction pipeline (no human review)."""

from .pipeline import AutoPipelineError, run_auto

__all__ = ["AutoPipelineError", "run_auto"]

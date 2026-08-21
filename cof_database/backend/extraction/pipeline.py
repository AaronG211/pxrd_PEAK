"""Main processing pipeline with progress tracking."""
import asyncio
import logging
from pathlib import Path
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from backend.database import SessionLocal
from backend.models import Paper, COFEntity, GasAdsorption, Evidence
from backend.extraction.pdf_parser import parse_pdf
from backend.extraction.chunker import chunk_pages
from backend.extraction.paper_processor import extract_paper_metadata
from backend.extraction.cof_extractor import extract_cof_properties, merge_extractions

logger = logging.getLogger(__name__)

# Global progress store (paper_id -> {pct, step})
_progress: dict[str, dict] = {}


def get_progress(paper_id: str) -> dict:
    return _progress.get(paper_id, {"pct": 0, "step": "Queued"})


def _set_progress(paper_id: str, pct: int, step: str):
    _progress[paper_id] = {"pct": pct, "step": step}


async def _update_db_progress(paper_id: str, pct: int, step: str, status: str = "processing"):
    """Persist progress to database so polling works too."""
    async with SessionLocal() as session:
        paper = await session.get(Paper, paper_id)
        if paper:
            paper.progress_pct = pct
            paper.progress_step = step
            paper.status = status
            await session.commit()


async def process_paper(paper_id: str, file_path: str):
    """Full extraction pipeline for a single paper."""
    try:
        # Step 1: Parse PDF
        _set_progress(paper_id, 5, "Parsing PDF...")
        await _update_db_progress(paper_id, 5, "Parsing PDF...")

        parsed = parse_pdf(file_path)
        pages = parsed["pages"]
        full_text = "\n\n".join(p["text"] for p in pages)

        # Update paper with parsed info
        async with SessionLocal() as session:
            paper = await session.get(Paper, paper_id)
            if paper:
                paper.doi = parsed.get("doi") or paper.doi
                paper.title = parsed.get("title") or paper.title
                paper.page_count = parsed["page_count"]
                paper.status = "processing"
                await session.commit()

        # Step 2: Chunk
        _set_progress(paper_id, 10, "Chunking text...")
        await _update_db_progress(paper_id, 10, "Chunking text...")
        chunks = chunk_pages(pages)

        # Step 3: Extract metadata + detect COFs
        _set_progress(paper_id, 15, "Extracting metadata & detecting COFs...")
        await _update_db_progress(paper_id, 15, "Extracting metadata & detecting COFs...")
        metadata = await extract_paper_metadata(full_text)

        async with SessionLocal() as session:
            paper = await session.get(Paper, paper_id)
            if paper:
                paper.summary = metadata.get("summary")
                paper.authors = metadata.get("authors")
                paper.date = metadata.get("date")
                await session.commit()

        cof_names = metadata.get("cof_names", [])
        if not cof_names:
            _set_progress(paper_id, 100, "No COFs detected")
            await _update_db_progress(paper_id, 100, "No COFs detected", "completed")
            return

        # Step 4: Extract properties per COF
        total_cofs = len(cof_names)
        for cof_idx, cof_name in enumerate(cof_names):
            base_pct = 20 + int((cof_idx / total_cofs) * 70)
            _set_progress(paper_id, base_pct, f"Extracting: {cof_name} ({cof_idx+1}/{total_cofs})")
            await _update_db_progress(paper_id, base_pct, f"Extracting: {cof_name} ({cof_idx+1}/{total_cofs})")

            # Process each chunk for this COF
            chunk_results = []
            for ci, chunk in enumerate(chunks):
                chunk_pct = base_pct + int(((ci + 1) / len(chunks)) * (70 / total_cofs))
                _set_progress(paper_id, min(chunk_pct, 90),
                              f"Extracting: {cof_name} - chunk {ci+1}/{len(chunks)}")

                try:
                    result = await extract_cof_properties(
                        cof_name, chunk["text"], chunk["page_start"], chunk["page_end"]
                    )
                    chunk_results.append(result)
                except Exception as e:
                    logger.warning(f"Chunk {ci} extraction failed for {cof_name}: {e}")

            # Merge results
            merged = merge_extractions(chunk_results)

            # Store COF
            await _store_cof(paper_id, cof_name, merged)

        # Done
        _set_progress(paper_id, 100, "Completed")
        await _update_db_progress(paper_id, 100, "Completed", "completed")

    except Exception as e:
        logger.exception(f"Pipeline failed for paper {paper_id}: {e}")
        _set_progress(paper_id, 0, f"Failed: {str(e)[:100]}")
        async with SessionLocal() as session:
            paper = await session.get(Paper, paper_id)
            if paper:
                paper.status = "failed"
                paper.error_message = str(e)[:500]
                paper.progress_step = f"Failed: {str(e)[:100]}"
                await session.commit()
    finally:
        # Clean up progress after a delay
        await asyncio.sleep(30)
        _progress.pop(paper_id, None)


async def _store_cof(paper_id: str, cof_name: str, data: dict):
    """Persist a single COF entity and its related records."""
    identity = data.get("identity", {}) or {}
    props = data.get("properties", {}) or {}
    char = data.get("characterization", {}) or {}
    synth = data.get("synthesis", {}) or {}

    async with SessionLocal() as session:
        cof = COFEntity(
            paper_id=paper_id,
            cof_name=cof_name,
            linker_smiles=identity.get("linker_smiles"),
            linkage_type=identity.get("linkage_type"),
            topology=identity.get("topology"),
            dimension=identity.get("dimension"),
            bet_surface_area=_safe_float(props.get("bet_surface_area")),
            pore_size=_safe_float(props.get("pore_size")),
            pore_size_unit=props.get("pore_size_unit"),
            thermal_stability_celsius=_safe_float(props.get("thermal_stability_celsius")),
            chemical_stability=props.get("chemical_stability"),
            pxrd_peaks=char.get("pxrd_peaks"),
            pxrd_simulated_vs_experimental=char.get("pxrd_simulated_vs_experimental"),
            n2_isotherm_description=char.get("n2_isotherm_description"),
            solvent_system=synth.get("solvent_system"),
            catalyst=synth.get("catalyst"),
            reaction_temperature_celsius=_safe_float(synth.get("reaction_temperature_celsius")),
            reaction_time_hours=_safe_float(synth.get("reaction_time_hours")),
            reaction_conditions=synth.get("reaction_conditions"),
        )
        session.add(cof)
        await session.flush()

        # Gas adsorption records
        for ga in (data.get("gas_adsorption") or []):
            session.add(GasAdsorption(
                cof_id=cof.id,
                gas=ga.get("gas"),
                uptake=_safe_float(ga.get("uptake")),
                unit=ga.get("unit"),
                temperature_k=_safe_float(ga.get("temperature_k")),
                pressure_bar=_safe_float(ga.get("pressure_bar")),
            ))

        # Evidence records
        for ev in (data.get("evidence") or []):
            if ev.get("text"):
                session.add(Evidence(
                    cof_id=cof.id,
                    property_name=ev.get("property", "unknown"),
                    source_text=ev["text"],
                    page_number=ev.get("page"),
                ))

        await session.commit()


def _safe_float(val) -> float | None:
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None

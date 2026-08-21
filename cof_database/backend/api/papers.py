"""Paper CRUD + upload + SSE progress endpoints."""
import asyncio
import logging
import shutil
import uuid
from pathlib import Path

from fastapi import APIRouter, UploadFile, File, BackgroundTasks, Depends, HTTPException
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sse_starlette.sse import EventSourceResponse

from backend.database import get_db
from backend.models import Paper, COFEntity, GasAdsorption, Evidence
from backend.schemas import PaperListOut, PaperDetailOut, COFOut, GasAdsorptionOut, EvidenceOut
from backend.extraction.pipeline import process_paper, get_progress
from backend.config import UPLOAD_DIR, SAMPLE_DIR

router = APIRouter(prefix="/api/papers", tags=["papers"])
logger = logging.getLogger(__name__)


# ── Upload ──────────────────────────────────────────────
@router.post("/upload")
async def upload_paper(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    try:
        UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

        paper_id = str(uuid.uuid4())
        ext = Path(file.filename or "paper.pdf").suffix
        dest = UPLOAD_DIR / f"{paper_id}{ext}"

        with open(dest, "wb") as f:
            shutil.copyfileobj(file.file, f)

        paper = Paper(id=paper_id, filename=file.filename or "unknown.pdf", status="pending")
        db.add(paper)
        await db.commit()

        background_tasks.add_task(_run_pipeline, paper_id, str(dest))
        return {"id": paper_id, "status": "pending"}
    except Exception as e:
        await db.rollback()
        logger.exception("Upload failed for %s: %s", file.filename, e)
        raise HTTPException(500, f"Upload failed: {str(e)[:300]}")


# ── Batch import from sample_papers/ ────────────────────
@router.post("/batch")
async def batch_import(
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    if not SAMPLE_DIR.exists():
        raise HTTPException(404, "sample_papers directory not found")

    pdfs = sorted(SAMPLE_DIR.glob("*.pdf"))
    imported = 0
    skipped = 0

    for pdf_path in pdfs:
        # Check if already imported (by filename)
        existing = await db.execute(select(Paper).where(Paper.filename == pdf_path.name))
        if existing.scalar_one_or_none():
            skipped += 1
            continue

        paper_id = str(uuid.uuid4())
        dest = UPLOAD_DIR / f"{paper_id}.pdf"
        shutil.copy2(pdf_path, dest)

        paper = Paper(id=paper_id, filename=pdf_path.name, status="pending")
        db.add(paper)
        await db.flush()

        background_tasks.add_task(_run_pipeline, paper_id, str(dest))
        imported += 1

    await db.commit()
    return {"imported": imported, "skipped": skipped, "total_files": len(pdfs)}


# ── List papers ─────────────────────────────────────────
@router.get("", response_model=list[PaperListOut])
async def list_papers(skip: int = 0, limit: int = 100, db: AsyncSession = Depends(get_db)):
    stmt = (
        select(Paper)
        .options(selectinload(Paper.cofs))
        .order_by(Paper.upload_time.desc())
        .offset(skip)
        .limit(limit)
    )
    result = await db.execute(stmt)
    papers = result.scalars().all()

    out = []
    for p in papers:
        # Get latest progress from in-memory store
        prog = get_progress(p.id)
        out.append(PaperListOut(
            id=p.id,
            doi=p.doi,
            title=p.title,
            filename=p.filename,
            authors=p.authors,
            date=p.date,
            status=p.status,
            page_count=p.page_count,
            summary=p.summary,
            cof_count=len(p.cofs),
            progress_pct=prog["pct"] if p.status == "processing" else (p.progress_pct or (100 if p.status == "completed" else 0)),
            progress_step=prog["step"] if p.status == "processing" else (p.progress_step or p.status),
        ))
    return out


# ── Paper detail ────────────────────────────────────────
@router.get("/{paper_id}", response_model=PaperDetailOut)
async def get_paper(paper_id: str, db: AsyncSession = Depends(get_db)):
    stmt = (
        select(Paper)
        .options(
            selectinload(Paper.cofs)
            .selectinload(COFEntity.gas_adsorption_records),
            selectinload(Paper.cofs)
            .selectinload(COFEntity.evidence_records),
        )
        .where(Paper.id == paper_id)
    )
    result = await db.execute(stmt)
    paper = result.scalar_one_or_none()
    if not paper:
        raise HTTPException(404, "Paper not found")

    prog = get_progress(paper.id)

    return PaperDetailOut(
        id=paper.id,
        doi=paper.doi,
        title=paper.title,
        filename=paper.filename,
        authors=paper.authors,
        date=paper.date,
        status=paper.status,
        page_count=paper.page_count,
        summary=paper.summary,
        error_message=paper.error_message,
        cof_count=len(paper.cofs),
        progress_pct=prog["pct"] if paper.status == "processing" else (paper.progress_pct or 0),
        progress_step=prog["step"] if paper.status == "processing" else (paper.progress_step or paper.status),
        cofs=[
            COFOut(
                id=c.id,
                cof_name=c.cof_name,
                linker_smiles=c.linker_smiles,
                linkage_type=c.linkage_type,
                topology=c.topology,
                dimension=c.dimension,
                bet_surface_area=c.bet_surface_area,
                pore_size=c.pore_size,
                pore_size_unit=c.pore_size_unit,
                thermal_stability_celsius=c.thermal_stability_celsius,
                chemical_stability=c.chemical_stability,
                pxrd_peaks=c.pxrd_peaks,
                pxrd_simulated_vs_experimental=c.pxrd_simulated_vs_experimental,
                n2_isotherm_description=c.n2_isotherm_description,
                solvent_system=c.solvent_system,
                catalyst=c.catalyst,
                reaction_temperature_celsius=c.reaction_temperature_celsius,
                reaction_time_hours=c.reaction_time_hours,
                reaction_conditions=c.reaction_conditions,
                gas_adsorption=[
                    GasAdsorptionOut(
                        gas=g.gas, uptake=g.uptake, unit=g.unit,
                        temperature_k=g.temperature_k, pressure_bar=g.pressure_bar,
                    )
                    for g in c.gas_adsorption_records
                ],
                evidence=[
                    EvidenceOut(
                        property_name=e.property_name,
                        source_text=e.source_text,
                        page_number=e.page_number,
                    )
                    for e in c.evidence_records
                ],
            )
            for c in paper.cofs
        ],
    )


# ── SSE progress stream ────────────────────────────────
@router.get("/{paper_id}/progress")
async def paper_progress_stream(paper_id: str):
    """Server-Sent Events endpoint for real-time progress."""
    async def event_generator():
        import json
        last_pct = -1
        while True:
            prog = get_progress(paper_id)
            pct = prog["pct"]
            step = prog["step"]

            if pct != last_pct:
                yield {
                    "event": "progress",
                    "data": json.dumps({"pct": pct, "step": step}),
                }
                last_pct = pct

            if pct >= 100 or "Failed" in step:
                yield {
                    "event": "done",
                    "data": json.dumps({"pct": pct, "step": step}),
                }
                break

            await asyncio.sleep(1)

    return EventSourceResponse(event_generator())


# ── Delete paper ────────────────────────────────────────
@router.delete("/{paper_id}")
async def delete_paper(paper_id: str, db: AsyncSession = Depends(get_db)):
    paper = await db.get(Paper, paper_id)
    if not paper:
        raise HTTPException(404, "Paper not found")
    await db.delete(paper)
    await db.commit()
    return {"deleted": paper_id}


# ── Helper ──────────────────────────────────────────────
async def _run_pipeline(paper_id: str, file_path: str):
    await process_paper(paper_id, file_path)

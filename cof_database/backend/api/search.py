"""Search endpoint for COF entities."""
from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.database import get_db
from backend.models import COFEntity
from backend.schemas import COFOut, GasAdsorptionOut, EvidenceOut

router = APIRouter(prefix="/api/search", tags=["search"])


@router.get("", response_model=list[COFOut])
async def search_cofs(
    bet_min: float | None = Query(None),
    bet_max: float | None = Query(None),
    linkage: str | None = Query(None),
    dimension: str | None = Query(None),
    cof_name: str | None = Query(None),
    gas: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(COFEntity).options(
        selectinload(COFEntity.gas_adsorption_records),
        selectinload(COFEntity.evidence_records),
    )

    if bet_min is not None:
        stmt = stmt.where(COFEntity.bet_surface_area >= bet_min)
    if bet_max is not None:
        stmt = stmt.where(COFEntity.bet_surface_area <= bet_max)
    if linkage:
        stmt = stmt.where(COFEntity.linkage_type.ilike(f"%{linkage}%"))
    if dimension:
        stmt = stmt.where(COFEntity.dimension == dimension)
    if cof_name:
        stmt = stmt.where(COFEntity.cof_name.ilike(f"%{cof_name}%"))

    result = await db.execute(stmt)
    cofs = result.scalars().all()

    out = []
    for c in cofs:
        # Filter by gas type if requested
        gas_records = c.gas_adsorption_records
        if gas:
            gas_records = [g for g in gas_records if g.gas and gas.lower() in g.gas.lower()]
            if not gas_records and gas:
                continue  # Skip COFs without matching gas data

        out.append(COFOut(
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
                for g in gas_records
            ],
            evidence=[
                EvidenceOut(
                    property_name=e.property_name,
                    source_text=e.source_text,
                    page_number=e.page_number,
                )
                for e in c.evidence_records
            ],
        ))

    return out

from pydantic import BaseModel
from typing import Optional


class GasAdsorptionOut(BaseModel):
    gas: Optional[str] = None
    uptake: Optional[float] = None
    unit: Optional[str] = None
    temperature_k: Optional[float] = None
    pressure_bar: Optional[float] = None

    class Config:
        from_attributes = True


class EvidenceOut(BaseModel):
    property_name: str
    source_text: str
    page_number: Optional[int] = None

    class Config:
        from_attributes = True


class COFOut(BaseModel):
    id: str
    cof_name: Optional[str] = None
    linker_smiles: Optional[str] = None
    linkage_type: Optional[str] = None
    topology: Optional[str] = None
    dimension: Optional[str] = None
    bet_surface_area: Optional[float] = None
    pore_size: Optional[float] = None
    pore_size_unit: Optional[str] = None
    thermal_stability_celsius: Optional[float] = None
    chemical_stability: Optional[str] = None
    pxrd_peaks: Optional[str] = None
    pxrd_simulated_vs_experimental: Optional[str] = None
    n2_isotherm_description: Optional[str] = None
    solvent_system: Optional[str] = None
    catalyst: Optional[str] = None
    reaction_temperature_celsius: Optional[float] = None
    reaction_time_hours: Optional[float] = None
    reaction_conditions: Optional[str] = None
    gas_adsorption: list[GasAdsorptionOut] = []
    evidence: list[EvidenceOut] = []

    class Config:
        from_attributes = True


class PaperListOut(BaseModel):
    id: str
    doi: Optional[str] = None
    title: Optional[str] = None
    filename: str
    authors: Optional[str] = None
    date: Optional[str] = None
    status: str
    page_count: Optional[int] = None
    summary: Optional[str] = None
    cof_count: int = 0
    progress_pct: int = 0
    progress_step: str = "Queued"

    class Config:
        from_attributes = True


class PaperDetailOut(PaperListOut):
    error_message: Optional[str] = None
    cofs: list[COFOut] = []

    class Config:
        from_attributes = True

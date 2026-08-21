import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, Text, Float, Integer, DateTime, ForeignKey, Boolean
from sqlalchemy.orm import relationship
from backend.database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _utcnow_naive() -> datetime:
    """Store UTC timestamps without tzinfo to match the current DB column type."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Paper(Base):
    __tablename__ = "papers"

    id = Column(String, primary_key=True, default=_uuid)
    doi = Column(String, nullable=True, unique=True)
    title = Column(Text, nullable=True)
    authors = Column(Text, nullable=True)
    date = Column(String, nullable=True)
    filename = Column(String, nullable=False)
    upload_time = Column(DateTime, default=_utcnow_naive)
    status = Column(String, default="pending")  # pending | processing | completed | failed
    page_count = Column(Integer, nullable=True)
    summary = Column(Text, nullable=True)
    error_message = Column(Text, nullable=True)

    # progress tracking
    progress_pct = Column(Integer, default=0)
    progress_step = Column(String, default="Queued")

    cofs = relationship("COFEntity", back_populates="paper", cascade="all, delete-orphan")


class COFEntity(Base):
    __tablename__ = "cof_entities"

    id = Column(String, primary_key=True, default=_uuid)
    paper_id = Column(String, ForeignKey("papers.id", ondelete="CASCADE"), nullable=False)

    # Chemical Identity
    cof_name = Column(String, nullable=True)
    linker_smiles = Column(Text, nullable=True)
    linkage_type = Column(String, nullable=True)
    topology = Column(String, nullable=True)
    dimension = Column(String, nullable=True)

    # Quantitative Properties
    bet_surface_area = Column(Float, nullable=True)
    pore_size = Column(Float, nullable=True)
    pore_size_unit = Column(String, nullable=True)  # nm or A
    thermal_stability_celsius = Column(Float, nullable=True)
    chemical_stability = Column(Text, nullable=True)

    # Characterization
    pxrd_peaks = Column(Text, nullable=True)
    pxrd_simulated_vs_experimental = Column(Text, nullable=True)
    n2_isotherm_description = Column(Text, nullable=True)

    # Synthesis Recipe
    solvent_system = Column(Text, nullable=True)
    catalyst = Column(Text, nullable=True)
    reaction_temperature_celsius = Column(Float, nullable=True)
    reaction_time_hours = Column(Float, nullable=True)
    reaction_conditions = Column(Text, nullable=True)

    paper = relationship("Paper", back_populates="cofs")
    gas_adsorption_records = relationship("GasAdsorption", back_populates="cof", cascade="all, delete-orphan")
    evidence_records = relationship("Evidence", back_populates="cof", cascade="all, delete-orphan")


class GasAdsorption(Base):
    __tablename__ = "gas_adsorption"

    id = Column(String, primary_key=True, default=_uuid)
    cof_id = Column(String, ForeignKey("cof_entities.id", ondelete="CASCADE"), nullable=False)
    gas = Column(String, nullable=True)
    uptake = Column(Float, nullable=True)
    unit = Column(String, nullable=True)
    temperature_k = Column(Float, nullable=True)
    pressure_bar = Column(Float, nullable=True)

    cof = relationship("COFEntity", back_populates="gas_adsorption_records")


class Evidence(Base):
    __tablename__ = "evidence"

    id = Column(String, primary_key=True, default=_uuid)
    cof_id = Column(String, ForeignKey("cof_entities.id", ondelete="CASCADE"), nullable=False)
    property_name = Column(String, nullable=False)
    source_text = Column(Text, nullable=False)
    page_number = Column(Integer, nullable=True)

    cof = relationship("COFEntity", back_populates="evidence_records")

"""Extract detailed COF properties from paper chunks."""
from backend.extraction.llm_client import call_llm

SYSTEM_PROMPT = """You are a scientific data extraction agent for Covalent Organic Frameworks (COFs).

You will receive a chunk of text from a research paper, and the name of a specific COF material.
Extract ONLY properties that belong to this specific COF. Do NOT mix data from other COFs.

For each property you extract, you MUST include the original sentence from the paper as evidence.

Extract the following categories:

1. Chemical Identity:
   - linker_smiles: SMILES strings of the monomers/linkers used to build this COF (if available)
   - linkage_type: the bond type (e.g., "Imine", "Boroxine", "Triazine", "beta-ketoenamine", "C-C")
   - topology: network topology (e.g., "hcb", "sql", "kgm", "dia", "bor")
   - dimension: "2D" or "3D"

2. Quantitative Properties:
   - bet_surface_area: numeric value in m2/g
   - pore_size: numeric value
   - pore_size_unit: "nm" or "A" (angstrom)
   - thermal_stability_celsius: TGA decomposition temperature Td in C
   - chemical_stability: description of stability in acid/base/water (or "stable"/"unstable")

3. Characterization:
   - pxrd_peaks: main 2theta peak positions (comma-separated, e.g., "4.7, 8.1, 9.4")
   - pxrd_simulated_vs_experimental: note if PXRD pattern matches simulated ("good match", "partial match", etc.)
   - n2_isotherm_description: description of N2 adsorption isotherm shape/type

4. Synthesis Recipe:
   - solvent_system: solvents and ratios (e.g., "o-DCB/n-BuOH (1:1)")
   - catalyst: catalyst type and concentration (e.g., "6 M HOAc")
   - reaction_temperature_celsius: in Celsius
   - reaction_time_hours: in hours
   - reaction_conditions: other conditions (vacuum, light-protected, etc.)

5. Gas Adsorption Performance:
   Array of measurements: [{gas, uptake, unit, temperature_k, pressure_bar}]

6. Evidence:
   For EACH property extracted, include the original sentence and page range.

Return JSON:
{
  "identity": {
    "linker_smiles": "string or null",
    "linkage_type": "string or null",
    "topology": "string or null",
    "dimension": "string or null"
  },
  "properties": {
    "bet_surface_area": number or null,
    "pore_size": number or null,
    "pore_size_unit": "string or null",
    "thermal_stability_celsius": number or null,
    "chemical_stability": "string or null"
  },
  "characterization": {
    "pxrd_peaks": "string or null",
    "pxrd_simulated_vs_experimental": "string or null",
    "n2_isotherm_description": "string or null"
  },
  "synthesis": {
    "solvent_system": "string or null",
    "catalyst": "string or null",
    "reaction_temperature_celsius": number or null,
    "reaction_time_hours": number or null,
    "reaction_conditions": "string or null"
  },
  "gas_adsorption": [
    {"gas": "CO2", "uptake": 3.2, "unit": "mmol/g", "temperature_k": 273, "pressure_bar": 1.0}
  ],
  "evidence": [
    {"property": "bet_surface_area", "text": "original sentence", "page": 5}
  ]
}

If a property is not found in this chunk, use null. Do NOT fabricate data."""


async def extract_cof_properties(cof_name: str, chunk_text: str, page_start: int, page_end: int) -> dict:
    """Extract properties for a single COF from a single chunk."""
    user_prompt = f"""COF name: {cof_name}
Text chunk (pages {page_start}-{page_end}):

{chunk_text}"""

    return await call_llm(SYSTEM_PROMPT, user_prompt)


def merge_extractions(results: list[dict]) -> dict:
    """Merge extraction results from multiple chunks, preferring first non-null value."""
    merged: dict = {
        "identity": {},
        "properties": {},
        "characterization": {},
        "synthesis": {},
        "gas_adsorption": [],
        "evidence": [],
    }

    dict_keys = ["identity", "properties", "characterization", "synthesis"]

    for res in results:
        for key in dict_keys:
            section = res.get(key, {}) or {}
            for field, value in section.items():
                if value is not None and merged[key].get(field) is None:
                    merged[key][field] = value

        gas = res.get("gas_adsorption") or []
        for g in gas:
            if g and g.get("uptake") is not None:
                # Deduplicate by (gas, uptake, temperature_k)
                sig = (g.get("gas"), g.get("uptake"), g.get("temperature_k"))
                existing = {(x.get("gas"), x.get("uptake"), x.get("temperature_k")) for x in merged["gas_adsorption"]}
                if sig not in existing:
                    merged["gas_adsorption"].append(g)

        evidence = res.get("evidence") or []
        for e in evidence:
            if e and e.get("text"):
                merged["evidence"].append(e)

    return merged

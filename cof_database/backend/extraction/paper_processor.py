"""Extract paper-level metadata: summary, authors, date, COF names."""
from backend.extraction.llm_client import call_llm

SYSTEM_PROMPT = """You are a scientific data extraction agent specializing in Covalent Organic Frameworks (COFs).

Given text from a research paper, extract:
1. A 2-4 sentence summary of the paper's key findings and contributions
2. Authors (comma-separated string)
3. Publication date (YYYY-MM-DD or partial)
4. A list of ALL distinct COF material names discussed in this paper

IMPORTANT:
- Include ALL COFs, not just the main one
- Use exact names as they appear in the paper (e.g., "COF-5", "TpPa-1", "DAAQ-TFP COF")
- Do NOT include generic terms like "COF" alone unless it is actually the material name

Return JSON:
{
  "summary": "string",
  "authors": "string or null",
  "date": "string or null",
  "cof_names": ["name1", "name2", ...]
}"""


async def extract_paper_metadata(full_text: str) -> dict:
    """Extract paper-level metadata from the full paper text."""
    # Use first ~6000 chars + last ~2000 chars for metadata
    text_for_meta = full_text[:6000]
    if len(full_text) > 8000:
        text_for_meta += "\n\n...[middle content omitted]...\n\n" + full_text[-2000:]

    result = await call_llm(SYSTEM_PROMPT, text_for_meta)
    return {
        "summary": result.get("summary"),
        "authors": result.get("authors"),
        "date": result.get("date"),
        "cof_names": result.get("cof_names", []),
    }

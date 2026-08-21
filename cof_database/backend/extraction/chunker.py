"""Split parsed PDF pages into overlapping chunks for LLM processing."""


def chunk_pages(pages: list[dict], chunk_size: int = 3, overlap: int = 1) -> list[dict]:
    """Group pages into chunks with overlap.

    Returns list of {text: str, page_start: int, page_end: int}.
    """
    chunks: list[dict] = []
    step = max(1, chunk_size - overlap)

    for i in range(0, len(pages), step):
        group = pages[i : i + chunk_size]
        if not group:
            break
        combined_text = "\n\n".join(p["text"] for p in group)
        chunks.append({
            "text": combined_text,
            "page_start": group[0]["page"],
            "page_end": group[-1]["page"],
        })

    return chunks

"""
Embedding Service — RAG (Retrieval-Augmented Generation) support.

Responsibilities:
  1. chunk_transcript_text(text)       — split long transcript text into ~300-word chunks
  2. generate_embedding(text)          — call Gemini text-embedding-004 to get a 768-dim vector
  3. save_embeddings(...)              — chunk a transcript and save all vectors to DB
  4. vector_search(question, user_id)  — find the top-N most relevant chunks for a question
"""
import logging
from datetime import date, datetime
from typing import List, Optional
from uuid import UUID
import numpy as np
from google import genai
from sqlalchemy.orm import Session

from app.config import settings
from app.models.transcript_embedding import TranscriptEmbedding

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────
CHUNK_SIZE_WORDS  = 300   # Target words per chunk
CHUNK_OVERLAP     = 50    # Words of overlap between consecutive chunks (context continuity)
EMBEDDING_MODEL   = "models/gemini-embedding-2"  # Google — 768-dim output
TOP_K_RESULTS     = 6     # How many chunks to retrieve per question


# ── 1. Chunking ──────────────────────────────────────────────────────────────

def chunk_transcript_text(full_text: str) -> List[str]:
    """
    Split a long string into overlapping word-based chunks.
    Overlap helps the AI not miss context that falls across a chunk boundary.

    Returns:
        List of text chunk strings.
    """
    words = full_text.split()
    if not words:
        return []

    chunks = []
    start = 0
    while start < len(words):
        end = start + CHUNK_SIZE_WORDS
        chunk = " ".join(words[start:end])
        chunks.append(chunk)
        start += CHUNK_SIZE_WORDS - CHUNK_OVERLAP  # slide with overlap

    return chunks


# ── 2. Embedding generation ──────────────────────────────────────────────────

def generate_embedding(text: str) -> Optional[List[float]]:
    """
    Convert a string of text into a 768-dimensional vector using Google's
    text-embedding-004 model.

    Returns:
        List of 768 floats, or None if the call fails.
    """
    try:
        from google.genai import types
        client = genai.Client(api_key=settings.GEMINI_API_KEY)
        result = client.models.embed_content(
            model=EMBEDDING_MODEL,
            contents=text,
            config=types.EmbedContentConfig(output_dimensionality=768)
        )
        return result.embeddings[0].values
    except Exception as e:
        logger.error("Embedding generation failed: %s", e)
        return None

def generate_embeddings_batch(texts: List[str]) -> Optional[List[List[float]]]:
    """
    Convert a list of strings into a list of 768-dimensional vectors using Google's
    text-embedding-004 model.

    Returns:
        List of 768-dimensional vectors, or None if the call fails.
    """
    if not texts:
        return []
    try:
        from google.genai import types
        client = genai.Client(api_key=settings.GEMINI_API_KEY)
        result = client.models.embed_content(
            model=EMBEDDING_MODEL,
            contents=texts,
            config=types.EmbedContentConfig(output_dimensionality=768)
        )
        return [emb.values for emb in result.embeddings]
    except Exception as e:
        logger.error("Batch embedding generation failed: %s", e)
        return None


# ── 3. Save embeddings for a transcript ─────────────────────────────────────

def save_embeddings(
    db: Session,
    transcript_id: UUID,
    user_id: UUID,
    full_text: str,
    metadata_prefix: str = "",
) -> int:
    """
    Chunk the transcript text, generate an embedding for each chunk, and
    persist them all to the transcript_embeddings table.

    Deletes any existing embeddings for this transcript first so that calling
    this function after an append-update stays consistent.

    Returns:
        Number of chunks saved.
    """
    # Delete stale embeddings for this transcript (e.g. on re-process / append)
    db.query(TranscriptEmbedding).filter(
        TranscriptEmbedding.transcript_id == transcript_id
    ).delete()
    db.flush()

    chunks = chunk_transcript_text(full_text)
    saved  = 0
    
    import time

    for idx, chunk in enumerate(chunks):
        if metadata_prefix:
            chunk = f"{metadata_prefix}\n{chunk}"
            
        vector = generate_embedding(chunk)
        
        # If rate limited, back off and retry once
        if vector is None:
            logger.warning("Embedding failed for chunk %d, retrying in 5s...", idx)
            time.sleep(5)
            vector = generate_embedding(chunk)
            
        if vector is None:
            logger.error("Skipping chunk %d for transcript %s — embedding failed after retry", idx, transcript_id)
            continue

        row = TranscriptEmbedding(
            transcript_id = transcript_id,
            user_id       = user_id,
            chunk_index   = idx,
            chunk_text    = chunk,
            embedding     = vector,
        )
        db.add(row)
        saved += 1
        
        # Sleep to respect rate limits (e.g., free tier allows ~15 requests/minute)
        # We sleep 4 seconds between chunks to stay under the limit.
        # Since this runs in a background thread, it won't block the user.
        time.sleep(4)

    db.commit()
    logger.info("Saved %d embedding chunks for transcript %s", saved, transcript_id)
    return saved


# ── 4. Cosine similarity helper ──────────────────────────────────────────────

def _cosine_similarity(a: List[float], b: List[float]) -> float:
    """Compute cosine similarity between two vectors using numpy."""
    va = np.array(a, dtype=np.float32)
    vb = np.array(b, dtype=np.float32)
    norm_a = np.linalg.norm(va)
    norm_b = np.linalg.norm(vb)
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return float(np.dot(va, vb) / (norm_a * norm_b))


# ── 5. Vector search ─────────────────────────────────────────────────────────

def vector_search(
    db: Session,
    user_id: UUID,
    question: str,
    top_k: int = TOP_K_RESULTS,
    target_date: Optional[date] = None,
) -> List[str]:
    """
    Convert the user's question into a vector, then find the most semantically
    similar transcript chunks using cosine similarity computed in Python (numpy).

    No PostgreSQL extension required — works with plain JSON columns.

    Returns:
        List of raw chunk text strings (most relevant first).
    """
    question_vector = generate_embedding(question)
    if question_vector is None:
        logger.warning("Vector search skipped — question embedding failed")
        return []

    # Fetch embedding rows for this user (optionally filtered by target_date)
    q = db.query(TranscriptEmbedding).filter(
        TranscriptEmbedding.user_id == user_id,
        TranscriptEmbedding.embedding.isnot(None),
    )

    if target_date:
        from app.models.transcript import Transcript
        start = datetime.combine(target_date, datetime.min.time())
        end = datetime.combine(target_date, datetime.max.time())
        q = q.join(Transcript).filter(
            Transcript.processing_timestamp >= start,
            Transcript.processing_timestamp <= end
        )

    rows = q.all()

    if not rows:
        logger.info("No embeddings found for user %s", user_id)
        return []

    # Score each chunk
    scored = []
    for row in rows:
        if row.embedding is not None:
            score = _cosine_similarity(question_vector, row.embedding)
            scored.append((score, row.chunk_text))

    # Sort by highest similarity and return top-k
    scored.sort(key=lambda x: x[0], reverse=True)
    top_chunks = [text for _, text in scored[:top_k]]

    logger.info("Vector search returned %d chunks for user %s", len(top_chunks), user_id)
    return top_chunks

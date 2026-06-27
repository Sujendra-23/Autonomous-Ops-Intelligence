from app.services.chunker import chunk_transcript


def test_empty_input_returns_no_chunks() -> None:
    assert chunk_transcript("") == []
    assert chunk_transcript("   \n\n  ") == []


def test_short_transcript_is_one_chunk() -> None:
    text = "Hello there.\n\nThis is short."
    chunks = chunk_transcript(text)
    assert len(chunks) == 1
    assert chunks[0].index == 0
    assert "Hello there" in chunks[0].content


def test_long_transcript_is_split_with_overlap() -> None:
    paragraph = ("Sentence one. " * 30 + "\n\n") * 8  # ~ 4500 chars
    chunks = chunk_transcript(paragraph, target_chars=2400, overlap_chars=200)
    assert len(chunks) > 1
    # Adjacent chunks should share at least one common substring (overlap).
    assert any(
        chunks[i].content[-50:] in chunks[i + 1].content[:300]
        for i in range(len(chunks) - 1)
    )


def test_chunks_have_monotonic_indices() -> None:
    text = ("Paragraph. " * 200 + "\n\n") * 5
    chunks = chunk_transcript(text, target_chars=1500, overlap_chars=100)
    indices = [c.index for c in chunks]
    assert indices == sorted(indices)
    assert indices[0] == 0


def test_fallback_window_for_single_blob() -> None:
    blob = "x" * 10_000
    chunks = chunk_transcript(blob, target_chars=2000, overlap_chars=200)
    assert len(chunks) > 1
    assert all(len(c.content) <= 2000 for c in chunks)

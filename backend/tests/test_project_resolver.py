from app.services.project_resolver import slugify


def test_slugify_handles_spaces_and_case() -> None:
    assert slugify("API Migration v2") == "api-migration-v2"


def test_slugify_collapses_punctuation() -> None:
    assert slugify("  Hello, World!! ") == "hello-world"


def test_slugify_fallback_on_empty() -> None:
    assert slugify("") == "project"
    assert slugify("!!!") == "project"


def test_slugify_is_bounded() -> None:
    out = slugify("x" * 200)
    assert len(out) <= 64

"""Tests for sorter folder browsing, including trailing-whitespace folder names."""

import os
import pytest
from fastapi import HTTPException

from media_library.routers.sorter import _resolve_directory_tolerant, browse_directory


def test_resolve_directory_tolerant_exact(tmp_path):
    d = tmp_path / "Movies"
    d.mkdir()
    assert _resolve_directory_tolerant(str(d)) == str(d)


def test_resolve_directory_tolerant_trailing_space(tmp_path):
    d = tmp_path / "Movies "  # note the trailing space
    d.mkdir()
    # A path typed without the trailing space should still resolve.
    assert _resolve_directory_tolerant(str(tmp_path / "Movies")) == str(d)


def test_resolve_directory_tolerant_missing(tmp_path):
    missing = tmp_path / "Nope"
    # No matching directory -> returns the abspath'd input unchanged.
    assert _resolve_directory_tolerant(str(missing)) == str(missing)


def test_resolve_directory_tolerant_ambiguous(tmp_path):
    exact = tmp_path / "Movies"
    exact.mkdir()
    spaced = tmp_path / "Movies "
    spaced.mkdir()
    # Exact match wins when both an exact and a spaced name exist.
    assert _resolve_directory_tolerant(str(exact)) == str(exact)


def test_browse_directory_trailing_space(tmp_path):
    parent = tmp_path / "arielrebel"
    parent.mkdir()
    movies = parent / "Movies "  # trailing space
    movies.mkdir()
    (movies / "sub").mkdir()

    # Browsing without the trailing space still resolves and lists contents.
    data = browse_directory(str(parent / "Movies"))
    assert data["current_path"] == str(movies)
    assert [f["name"] for f in data["folders"]] == ["sub"]


def test_browse_directory_missing_returns_404(tmp_path):
    with pytest.raises(HTTPException) as exc_info:
        browse_directory(str(tmp_path / "does_not_exist"))
    assert exc_info.value.status_code == 404

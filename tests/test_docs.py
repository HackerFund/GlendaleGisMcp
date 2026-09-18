import pytest

from glendale_gis.core.docs import read_doc


@pytest.mark.parametrize("name", ["real-time-sources.md", "snapshot-data.md"])
def test_docs_are_readable(name):
    assert read_doc(name).startswith("# ")


@pytest.mark.parametrize("name", ["../AGENTS.md", "..\\x", ".hidden", "sub/dir.md"])
def test_path_tricks_are_refused(name):
    with pytest.raises(ValueError):
        read_doc(name)


def test_missing_doc():
    with pytest.raises(FileNotFoundError):
        read_doc("nope.md")

from pathlib import Path

from learning_resources.resources import load_resource_index


def test_reviewed_resource_library_remains_available_without_promotion_runtime():
    index = load_resource_index()
    assert len(index) >= 250
    assert len(index["stack"]) >= 1
    resource = index["stack"][0]
    assert resource["resource_id"].startswith("resource:sha256:")
    assert resource["source_url"].startswith(("http://", "https://"))
    assert resource["license_url"].startswith(("http://", "https://"))
    assert resource["pages"] == sorted(set(resource["pages"]))


def test_corrupt_resource_library_adds_no_unreviewed_data(tmp_path: Path):
    path = tmp_path / "corrupt.json"
    path.write_text('{"schema":"resource-library/v1"}')
    assert load_resource_index(path) == {}

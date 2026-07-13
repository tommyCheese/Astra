from pathlib import Path

import pytest

from app.agent_profile import (
    AgentProfile,
    AgentProfileConfigurationError,
    AgentProfileLoader,
    ModelOperation,
    load_agent_profile,
)


def profile_contents() -> dict[str, str]:
    profile = AgentProfileLoader().load()
    return {document.name: document.content for document in profile.manifest.documents}


def test_packaged_profile_loads_outside_backend_working_directory(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)

    profile = AgentProfileLoader().load()

    assert profile.document("identity").filename == "IDENTITY.md"
    assert profile.document("autodream").status == "disabled"
    assert profile.manifest.version.startswith("profile-")


def test_profile_normalization_and_hashes_are_deterministic():
    contents = profile_contents()
    first = AgentProfileLoader().load(contents)
    contents["identity"] = contents["identity"].replace("\n", "\r\n") + "\r\n"
    second = AgentProfileLoader().load(contents)

    assert first.manifest.version == second.manifest.version
    assert first.document("identity").sha256 == second.document("identity").sha256


def test_profile_version_changes_with_document_content():
    contents = profile_contents()
    original = AgentProfileLoader().load(contents)
    contents["soul"] = contents["soul"].replace("Astra 真诚", "Astra 始终真诚", 1)

    changed = AgentProfileLoader().load(contents)

    assert original.manifest.version != changed.manifest.version


@pytest.mark.parametrize(
    "mutation, expected",
    [
        (lambda values: values.pop("identity"), "missing"),
        (
            lambda values: values.__setitem__(
                "identity", values["identity"].replace("## Mission", "## Missing Mission")
            ),
            "required section",
        ),
        (
            lambda values: values.__setitem__(
                "memory", values["memory"].replace("schema_version: 1", "schema_version: 2")
            ),
            "metadata",
        ),
    ],
)
def test_profile_rejects_missing_or_malformed_documents(mutation, expected):
    contents = profile_contents()
    mutation(contents)

    with pytest.raises(AgentProfileConfigurationError, match=expected):
        AgentProfileLoader().load(contents)


def test_profile_enforces_document_size_limit():
    contents = profile_contents()

    with pytest.raises(AgentProfileConfigurationError, match="exceeds"):
        AgentProfileLoader(max_document_bytes=100).load(contents)


def test_snapshot_reconstructs_exact_profile_without_exposing_content_in_manifest():
    profile = load_agent_profile()
    snapshot = profile.snapshot()

    reconstructed = AgentProfile.from_snapshot(snapshot)
    safe = profile.manifest.safe_dict()

    assert reconstructed.manifest.version == profile.manifest.version
    assert "content" not in safe["documents"]["identity"]
    assert snapshot["documents"]["identity"]["content"]


def test_current_model_operations_never_select_autodream():
    profile = load_agent_profile()

    for operation in ModelOperation:
        selected = {document.name for document in profile.documents_for(operation)}
        assert "autodream" not in selected


def test_live_database_is_not_a_packaged_profile_resource():
    profile_directory = Path(__file__).parents[1] / "app" / "agent_profile"

    assert not list(profile_directory.glob("*.db"))

import pytest

from app.repositories.runtime_profiles import (
    RuntimeBuildStateError,
    RuntimeProfileRepository,
)


async def test_runtime_build_state_machine_activates_profile_atomically(session):
    repository = RuntimeProfileRepository(session)
    profile = await repository.get_or_create_default(
        active_image="astra-data-viz:0.1.0",
        dependency_digest="base",
    )
    build = await repository.create_build(
        dependencies=[{"name": "polars", "version": "1.2.3"}],
        dependency_digest="digest-1",
    )

    with pytest.raises(RuntimeBuildStateError, match="runtime_build_in_progress"):
        await repository.create_build(dependencies=[], dependency_digest="other")

    await repository.transition(
        build.id,
        "building",
        phase="正在构建",
        progress=40,
        staging_image=f"astra-data-viz:build-{build.id}",
    )
    activated = await repository.activate(build.id, image="astra-data-viz:custom-digest-1")
    await session.commit()

    assert activated.status == "succeeded"
    assert activated.progress == 100
    assert profile.active_image == "astra-data-viz:custom-digest-1"
    assert profile.dependencies == [{"name": "polars", "version": "1.2.3"}]
    assert profile.version == 2


async def test_runtime_build_state_machine_rejects_invalid_transition_and_recovers(session):
    repository = RuntimeProfileRepository(session)
    await repository.get_or_create_default(
        active_image="astra-data-viz:0.1.0",
        dependency_digest="base",
    )
    build = await repository.create_build(dependencies=[], dependency_digest="empty")
    with pytest.raises(RuntimeBuildStateError, match="invalid_runtime_build_transition"):
        await repository.transition(build.id, "succeeded")

    recovered = await repository.recover_interrupted()
    await session.commit()
    assert recovered == (build.id,)
    assert build.status == "cancelled"
    assert build.error_code == "runtime_restarted"

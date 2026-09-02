from __future__ import annotations


async def test_statistics_empty(client) -> None:
    response = await client.get("/statistics")
    assert response.status_code == 200
    body = response.json()
    assert body["total_incidents"] == 0
    assert body["mean_recovery_time_seconds"] is None
    assert body["recovery_success_rate"] is None
    assert body["most_unstable_services"] == []
    assert body["failure_frequency"]["by_component"] == []
    assert body["recovery_frequency"]["by_strategy"] == []


async def test_statistics_reflect_incidents(client, state) -> None:
    from app.models.incident import IncidentStatus, IncidentUpdate

    one = await state.incidents.open(component="postgres", failure_type="stopped")
    two = await state.incidents.open(component="postgres", failure_type="stopped")
    await state.incidents.open(component="jellyfin", failure_type="stopped")

    # Simulate the orchestrator resolving two of them.
    await state.incidents.update(
        one.incident_id,
        IncidentUpdate(
            status=IncidentStatus.RESOLVED,
            recovery_strategy="docker_restart",
            recovery_result=True,
            duration=12.5,
            attempts=1,
        ),
    )
    await state.incidents.update(
        two.incident_id,
        IncidentUpdate(
            status=IncidentStatus.RESOLVED,
            recovery_strategy="docker_restart",
            recovery_result=True,
            duration=7.5,
            attempts=2,
        ),
    )

    response = await client.get("/statistics")
    body = response.json()
    assert body["total_incidents"] == 3
    assert body["mean_recovery_time_seconds"] == 10.0
    assert body["recovery_success_rate"] == 1.0

    unstable = body["most_unstable_services"]
    assert unstable[0]["component"] == "postgres"
    assert unstable[0]["incidents"] == 2

    by_component = {
        r["component"]: r["incidents"] for r in body["failure_frequency"]["by_component"]
    }
    assert by_component == {"postgres": 2, "jellyfin": 1}

    by_strategy = {
        r["strategy"]: r["recoveries"] for r in body["recovery_frequency"]["by_strategy"]
    }
    assert by_strategy == {"docker_restart": 2}

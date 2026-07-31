from __future__ import annotations

from workctx.validation import validate_workspace
from workctx.validation.engine import _cyclic_components

from .conftest import FixtureWorkspace


def _codes(workspace: FixtureWorkspace) -> list[str]:
    return [issue.code for issue in validate_workspace(workspace.root).issues]


def test_missing_task_parent_is_rejected(canonical_workspace: FixtureWorkspace) -> None:
    subtask_id = "TASK-2026-900-ST01"
    payload = canonical_workspace.task_payload(
        subtask_id,
        task_type="subtask",
        parent_task="TASK-2026-900",
        root_task="TASK-2026-900",
    )
    canonical_workspace.write_markdown("03_work/tasks", payload)

    assert _codes(canonical_workspace).count("TASK-HIERARCHY") == 1


def test_each_missing_task_parent_is_attributed_to_its_subtask(
    canonical_workspace: FixtureWorkspace,
) -> None:
    expected_paths: set[str] = set()
    for number in (910, 911):
        task_id = f"TASK-2026-{number}-ST01"
        parent_id = f"TASK-2026-{number}"
        payload = canonical_workspace.task_payload(
            task_id,
            task_type="subtask",
            parent_task=parent_id,
            root_task=parent_id,
        )
        path = canonical_workspace.write_markdown("03_work/tasks", payload)
        expected_paths.add(path.relative_to(canonical_workspace.root).as_posix())

    report = validate_workspace(canonical_workspace.root)
    actual_paths = {issue.path for issue in report.issues if issue.code == "TASK-HIERARCHY"}

    assert actual_paths == expected_paths


def test_foreign_task_hierarchy_issue_is_attributed_to_foreign_task(
    canonical_workspace: FixtureWorkspace,
) -> None:
    active = canonical_workspace.task_payload("TASK-2026-912")
    foreign = canonical_workspace.task_payload("TASK-2026-913")
    foreign["uri"] = "workctx://other-context/task/TASK-2026-913"
    canonical_workspace.write_markdown("03_work/tasks", active)
    foreign_path = canonical_workspace.write_markdown("03_work/tasks", foreign)

    report = validate_workspace(canonical_workspace.root)
    hierarchy_paths = [issue.path for issue in report.issues if issue.code == "TASK-HIERARCHY"]

    assert hierarchy_paths == [foreign_path.relative_to(canonical_workspace.root).as_posix()]


def test_blocks_and_depends_on_contradiction_cycle_is_rejected(
    canonical_workspace: FixtureWorkspace,
) -> None:
    first_id = "TASK-2026-901"
    second_id = "TASK-2026-902"
    first = canonical_workspace.task_payload(
        first_id,
        references=[
            {
                "relation": "depends_on",
                "target": canonical_workspace.uri("task", second_id),
            },
            {
                "relation": "blocks",
                "target": canonical_workspace.uri("task", second_id),
            },
        ],
    )
    second = canonical_workspace.task_payload(second_id)
    canonical_workspace.write_markdown("03_work/tasks", first)
    canonical_workspace.write_markdown("03_work/tasks", second)

    assert _codes(canonical_workspace).count("TASK-RELATION-CYCLE") == 1


def test_consistent_blocks_and_depends_on_pair_is_not_a_cycle(
    canonical_workspace: FixtureWorkspace,
) -> None:
    first_id = "TASK-2026-903"
    second_id = "TASK-2026-904"
    first = canonical_workspace.task_payload(
        first_id,
        references=[
            {
                "relation": "depends_on",
                "target": canonical_workspace.uri("task", second_id),
            }
        ],
    )
    second = canonical_workspace.task_payload(
        second_id,
        references=[
            {
                "relation": "blocks",
                "target": canonical_workspace.uri("task", first_id),
            }
        ],
    )
    canonical_workspace.write_markdown("03_work/tasks", first)
    canonical_workspace.write_markdown("03_work/tasks", second)

    assert "TASK-RELATION-CYCLE" not in _codes(canonical_workspace)


def test_plain_task_dependencies_participate_in_cycle_check(
    canonical_workspace: FixtureWorkspace,
) -> None:
    first_id = "TASK-2026-905"
    second_id = "TASK-2026-906"
    first = canonical_workspace.task_payload(first_id, dependencies=[second_id])
    second = canonical_workspace.task_payload(second_id, dependencies=[first_id])
    canonical_workspace.write_markdown("03_work/tasks", first)
    canonical_workspace.write_markdown("03_work/tasks", second)

    assert _codes(canonical_workspace).count("TASK-RELATION-CYCLE") == 1


def test_task_relation_must_target_a_task(canonical_workspace: FixtureWorkspace) -> None:
    task = canonical_workspace.task_payload(
        "TASK-2026-907",
        references=[
            {
                "relation": "depends_on",
                "target": canonical_workspace.uri("project", "PRJ-validation-lab"),
            }
        ],
    )
    canonical_workspace.write_markdown("03_work/tasks", task)

    assert _codes(canonical_workspace).count("TASK-RELATION-TARGET") == 1


def test_plain_non_task_dependency_has_target_diagnostic(
    canonical_workspace: FixtureWorkspace,
) -> None:
    task = canonical_workspace.task_payload(
        "TASK-2026-908",
        dependencies=["PRJ-validation-lab"],
    )
    canonical_workspace.write_markdown("03_work/tasks", task)

    codes = _codes(canonical_workspace)
    assert codes.count("TASK-RELATION-TARGET") == 1
    assert "REF-UNRESOLVED" not in codes


def test_large_converging_task_dag_does_not_recurse_or_report_a_cycle() -> None:
    graph = {f"node-{index}": {f"node-{index + 1}"} for index in range(5000)}
    graph["branch"] = {"node-1", "node-2"}
    graph["node-5000"] = set()

    assert _cyclic_components(graph) == []


def test_overlapping_current_claims_are_rejected(
    canonical_workspace: FixtureWorkspace,
) -> None:
    first = canonical_workspace.claim_payload(
        "CLM-2026-00001",
        valid_from="2026-07-01T00:00:00Z",
        valid_to="2026-08-01T00:00:00Z",
    )
    second = canonical_workspace.claim_payload(
        "CLM-2026-00002",
        valid_from="2026-07-15T00:00:00Z",
        valid_to="2026-08-15T00:00:00Z",
    )
    canonical_workspace.write_markdown("02_knowledge/claims", first)
    canonical_workspace.write_markdown("02_knowledge/claims", second)

    assert _codes(canonical_workspace).count("CLAIM-CURRENT-OVERLAP") == 1


def test_claim_object_keys_are_not_treated_as_reference_fields(
    canonical_workspace: FixtureWorkspace,
) -> None:
    claim = canonical_workspace.claim_payload("CLM-2026-00011")
    claim["object"] = {"target": "blue", "uri": "display-label"}
    canonical_workspace.write_markdown("02_knowledge/claims", claim)

    assert validate_workspace(canonical_workspace.root).issues == []


def test_adjacent_current_claim_intervals_do_not_overlap(
    canonical_workspace: FixtureWorkspace,
) -> None:
    first = canonical_workspace.claim_payload(
        "CLM-2026-00003",
        valid_from="2026-07-01T00:00:00Z",
        valid_to="2026-08-01T00:00:00Z",
    )
    second = canonical_workspace.claim_payload(
        "CLM-2026-00004",
        valid_from="2026-08-01T00:00:00Z",
        valid_to="2026-09-01T00:00:00Z",
    )
    canonical_workspace.write_markdown("02_knowledge/claims", first)
    canonical_workspace.write_markdown("02_knowledge/claims", second)

    assert "CLAIM-CURRENT-OVERLAP" not in _codes(canonical_workspace)


def test_empty_claim_interval_is_rejected(canonical_workspace: FixtureWorkspace) -> None:
    claim = canonical_workspace.claim_payload(
        "CLM-2026-00005",
        valid_from="2026-08-01T00:00:00Z",
        valid_to="2026-08-01T00:00:00Z",
    )
    canonical_workspace.write_markdown("02_knowledge/claims", claim)

    assert _codes(canonical_workspace).count("CLAIM-INTERVAL") == 1


def test_missing_supersession_target_is_rejected(
    canonical_workspace: FixtureWorkspace,
) -> None:
    claim = canonical_workspace.claim_payload(
        "CLM-2026-00006",
        status="superseded",
        supersedes="CLM-2026-99999",
    )
    canonical_workspace.write_markdown("02_knowledge/claims", claim)

    assert _codes(canonical_workspace).count("CLAIM-SUPERSESSION-MISSING") == 1


def test_supersession_cycle_is_rejected(canonical_workspace: FixtureWorkspace) -> None:
    first_id = "CLM-2026-00007"
    second_id = "CLM-2026-00008"
    first = canonical_workspace.claim_payload(
        first_id,
        status="superseded",
        supersedes=second_id,
    )
    second = canonical_workspace.claim_payload(
        second_id,
        status="superseded",
        supersedes=first_id,
    )
    canonical_workspace.write_markdown("02_knowledge/claims", first)
    canonical_workspace.write_markdown("02_knowledge/claims", second)

    assert _codes(canonical_workspace).count("CLAIM-SUPERSESSION-CYCLE") == 1


def test_reciprocal_supersession_fields_normalize_to_one_edge(
    canonical_workspace: FixtureWorkspace,
) -> None:
    old_id = "CLM-2026-00009"
    new_id = "CLM-2026-00010"
    old = canonical_workspace.claim_payload(
        old_id,
        status="superseded",
        superseded_by=new_id,
    )
    new = canonical_workspace.claim_payload(
        new_id,
        status="current",
        supersedes=old_id,
    )
    canonical_workspace.write_markdown("02_knowledge/claims", old)
    canonical_workspace.write_markdown("02_knowledge/claims", new)

    assert "CLAIM-SUPERSESSION-CYCLE" not in _codes(canonical_workspace)

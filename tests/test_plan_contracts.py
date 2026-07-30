import json
from pathlib import Path

from jsonschema import Draft202012Validator


ROOT = Path(__file__).parents[1]


def test_initial_backlog_dependencies_reference_existing_work_packages() -> None:
    backlog = json.loads(
        (ROOT / ".agents" / "plan" / "initial" / "initial-backlog.json").read_text(
            encoding="utf-8"
        )
    )
    ids = {item["id"] for item in backlog["work_packages"]}
    for item in backlog["work_packages"]:
        assert set(item["dependencies"]).issubset(ids)
        assert item["id"] not in item["dependencies"]


def test_work_order_template_validates_against_plan_schema() -> None:
    schema = json.loads(
        (ROOT / ".agents" / "plan" / "initial" / "agent-task-contract.schema.json").read_text(
            encoding="utf-8"
        )
    )
    contract = json.loads(
        (ROOT / ".agents" / "templates" / "work-order" / "contract.json").read_text(
            encoding="utf-8"
        )
    )
    Draft202012Validator(schema).validate(contract)


def test_worker_report_template_validates_against_plan_schema() -> None:
    schema = json.loads(
        (ROOT / ".agents" / "plan" / "initial" / "agent-report.schema.json").read_text(
            encoding="utf-8"
        )
    )
    report = json.loads(
        (ROOT / ".agents" / "templates" / "work-order" / "report.json").read_text(
            encoding="utf-8"
        )
    )
    Draft202012Validator(schema).validate(report)


def test_initial_backlog_is_acyclic_and_matches_dependency_graph() -> None:
    base = ROOT / ".agents" / "plan" / "initial"
    backlog = json.loads((base / "initial-backlog.json").read_text(encoding="utf-8"))
    graph = json.loads((base / "dependency-graph.json").read_text(encoding="utf-8"))

    dependencies = {
        item["id"]: set(item["dependencies"]) for item in backlog["work_packages"]
    }
    graph_edges = {(edge["from"], edge["to"]) for edge in graph["edges"]}
    expected_edges = {
        (dependency, work_package)
        for work_package, required in dependencies.items()
        for dependency in required
    }
    assert graph_edges == expected_edges

    temporary: set[str] = set()
    permanent: set[str] = set()

    def visit(node: str) -> None:
        if node in permanent:
            return
        assert node not in temporary, f"Dependency cycle at {node}"
        temporary.add(node)
        for dependency in dependencies[node]:
            visit(dependency)
        temporary.remove(node)
        permanent.add(node)

    for node in dependencies:
        visit(node)

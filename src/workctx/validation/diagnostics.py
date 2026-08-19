from __future__ import annotations

from dataclasses import dataclass

from workctx.validation.report import Severity


@dataclass(frozen=True, slots=True)
class DiagnosticDefinition:
    code: str
    severity: Severity
    cause: str
    repair_action: str


_DEFINITIONS = (
    DiagnosticDefinition(
        "CTX-CONFIG",
        Severity.ERROR,
        "context.yaml is missing, unreadable, or invalid for ContextConfig.",
        "Restore context.yaml and correct it to satisfy the current ContextConfig model.",
    ),
    DiagnosticDefinition(
        "CTX-FEDERATED-SEARCH",
        Severity.ERROR,
        "The Phase 1 context enables federated_search.",
        "Set policies.federated_search to false in context.yaml.",
    ),
    DiagnosticDefinition(
        "CTX-MISSING-DIRECTORY",
        Severity.ERROR,
        "A required top-level context directory is absent.",
        "Restore the named directory from the canonical context layout.",
    ),
    DiagnosticDefinition(
        "CTX-NON-UTF8",
        Severity.WARNING,
        "A workspace text file cannot be decoded as UTF-8.",
        "Convert the file to UTF-8 without changing its intended content.",
    ),
    DiagnosticDefinition(
        "CTX-UNREADABLE-PATH",
        Severity.ERROR,
        "A workspace file or directory could not be inspected.",
        "Restore read access and rerun validation so the path can be checked.",
    ),
    DiagnosticDefinition(
        "CTX-ABSOLUTE-PATH",
        Severity.WARNING,
        "A durable workspace value contains a machine-specific absolute path.",
        "Replace the path with a context-relative path or a canonical durable URI.",
    ),
    DiagnosticDefinition(
        "CTX-PATH-ESCAPE",
        Severity.ERROR,
        "A workspace link crosses a read boundary, or an artifact path is unsafe or non-portable.",
        "Remove the link or use a portable forward-slash artifact path inside the context root.",
    ),
    DiagnosticDefinition(
        "CTX-POSSIBLE-SECRET",
        Severity.ERROR,
        "A workspace text file contains a secret-looking assignment or private-key marker.",
        "Remove the value and store only an approved secret reference.",
    ),
    DiagnosticDefinition(
        "DOC-PARSE",
        Severity.ERROR,
        "A canonical document cannot be parsed as its declared Markdown, YAML, or JSON form.",
        "Repair the document syntax and required frontmatter delimiters.",
    ),
    DiagnosticDefinition(
        "DOC-MODEL",
        Severity.ERROR,
        "A canonical document does not satisfy its integrated domain model.",
        "Correct the document fields to satisfy the current typed domain contract.",
    ),
    DiagnosticDefinition(
        "DOC-FILENAME-ID",
        Severity.ERROR,
        "A canonical document filename does not match its frontmatter ID.",
        "Rename the file to its immutable ID while preserving the appropriate extension.",
    ),
    DiagnosticDefinition(
        "DOC-DUPLICATE-ID",
        Severity.ERROR,
        "More than one canonical document declares the same canonical identity.",
        "Keep one canonical owner of the ID and reconcile the duplicate document.",
    ),
    DiagnosticDefinition(
        "REF-INVALID-URI",
        Severity.ERROR,
        "A durable reference is malformed or not canonically encoded.",
        "Replace it with a canonical workctx://, artifact://, repo://, or external URI.",
    ),
    DiagnosticDefinition(
        "REF-REPO-SHA",
        Severity.ERROR,
        "A repository reference omits an immutable hexadecimal commit SHA.",
        "Replace the branch or tag with a 7-64 character commit SHA.",
    ),
    DiagnosticDefinition(
        "REF-CONTEXT-MISMATCH",
        Severity.ERROR,
        "A workctx:// reference crosses the active context security boundary.",
        "Point the reference at an entity in the active context or use an approved "
        "federated operation.",
    ),
    DiagnosticDefinition(
        "REF-UNKNOWN-ENTITY-TYPE",
        Severity.ERROR,
        "A workctx:// reference uses an entity type outside the D-018 vocabulary.",
        "Replace the entity-type segment with one of the canonical D-018 values.",
    ),
    DiagnosticDefinition(
        "REF-UNRESOLVED",
        Severity.ERROR,
        "An internal Work Context reference has no canonical target in the workspace.",
        "Restore the target entity or update the reference to an existing canonical URI.",
    ),
    DiagnosticDefinition(
        "REF-ARTIFACT-UNAVAILABLE",
        Severity.ADVISORY,
        "An artifact:// reference has no matching artifact manifest in the workspace.",
        "Restore or ingest the artifact manifest, or explicitly record that the artifact is "
        "unavailable.",
    ),
    DiagnosticDefinition(
        "REF-EXTERNAL-UNAVAILABLE",
        Severity.ADVISORY,
        "A valid repository or external reference cannot be resolved by the local engine.",
        "Configure an authorized resolver or explicitly retain the reference as unavailable.",
    ),
    DiagnosticDefinition(
        "OBS-INVALID",
        Severity.ERROR,
        "An embedded observation or its source locator violates the Observation model.",
        "Correct the observation fields, artifact reference, and exact source locator.",
    ),
    DiagnosticDefinition(
        "OBS-EVIDENCE-ID",
        Severity.ERROR,
        "An embedded observation ID does not belong to its containing evidence note.",
        "Change the observation ID to use the containing evidence ID plus #OBS-NNN.",
    ),
    DiagnosticDefinition(
        "TASK-HIERARCHY",
        Severity.ERROR,
        "The workspace task corpus violates parent, root, uniqueness, or context rules.",
        "Restore the missing parent or correct task_type, parent_task, root_task, IDs, and "
        "context.",
    ),
    DiagnosticDefinition(
        "TASK-RELATION-CYCLE",
        Severity.ERROR,
        "Normalized blocks and depends_on precedence relations form a cycle.",
        "Remove or reverse a contradictory task relation so prerequisites are acyclic.",
    ),
    DiagnosticDefinition(
        "TASK-RELATION-TARGET",
        Severity.ERROR,
        "A blocks or depends_on relation points to something other than a task.",
        "Replace the relation target with a canonical task ID or workctx:// task URI.",
    ),
    DiagnosticDefinition(
        "CLAIM-INTERVAL",
        Severity.ERROR,
        "A claim validity interval is empty or ends before it starts.",
        "Set valid_to after valid_from, or leave the appropriate bound open.",
    ),
    DiagnosticDefinition(
        "CLAIM-CURRENT-OVERLAP",
        Severity.ERROR,
        "Current claims for one subject and predicate have overlapping validity intervals.",
        "Close or supersede the older interval so only one current value applies at a time.",
    ),
    DiagnosticDefinition(
        "CLAIM-SUPERSESSION-MISSING",
        Severity.ERROR,
        "A supersedes or superseded_by claim ID has no canonical target.",
        "Restore the referenced claim or correct the supersession ID.",
    ),
    DiagnosticDefinition(
        "CLAIM-SUPERSESSION-CYCLE",
        Severity.ERROR,
        "Normalized claim supersession relations form a cycle.",
        "Break the cycle so supersession history progresses in one direction.",
    ),
    DiagnosticDefinition(
        "META-SCHEMA-STALE",
        Severity.ADVISORY,
        "A packaged reference schema under 99_meta/schemas/ is missing or differs "
        "from the installed version.",
        "Run workctx context refresh-meta to materialize the current packaged reference schemas.",
    ),
    DiagnosticDefinition(
        "PROJECTION-STALE",
        Severity.WARNING,
        "The configured freshness probe reports stale derived projections.",
        "Rebuild the derived projections from canonical workspace files.",
    ),
    DiagnosticDefinition(
        "PROJECTION-BACKLINK-MISMATCH",
        Severity.WARNING,
        "Generated backlinks do not match canonical outbound relations.",
        "Rebuild the backlink projection from the reported canonical edge set.",
    ),
    DiagnosticDefinition(
        "PROJECTION-FRESHNESS-UNKNOWN",
        Severity.ADVISORY,
        "Projection freshness cannot be determined by the configured probe.",
        "Configure a projection-aware freshness probe or verify projections independently.",
    ),
    DiagnosticDefinition(
        "PROJECTION-PROBE-FAILED",
        Severity.WARNING,
        "The configured freshness probe failed without completing its check.",
        "Inspect the probe integration and retry validation; canonical files were not changed.",
    ),
)

DIAGNOSTIC_DEFINITIONS: dict[str, DiagnosticDefinition] = {
    definition.code: definition for definition in _DEFINITIONS
}

if len(DIAGNOSTIC_DEFINITIONS) != len(_DEFINITIONS):  # pragma: no cover - import invariant
    raise RuntimeError("Diagnostic codes must be unique")

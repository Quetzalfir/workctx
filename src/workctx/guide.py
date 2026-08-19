"""Deterministic file-placement and ownership guidance for one resolved context."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import cast

from pydantic import JsonValue
from rich.console import Group, RenderableType
from rich.table import Table
from rich.text import Text

GUIDE_SCHEMA_VERSION = 1


class OwnershipClass(StrEnum):
    """Stable ownership classes exposed by the guide command."""

    CANONICAL_VIA_PROPOSALS = "canonical-via-proposals"
    OPERATOR_OWNED = "operator-owned"
    ADAPTER_MANAGED = "adapter-managed"
    GENERATED = "generated"
    MACHINE_STATE = "machine-state"


class EditPolicy(StrEnum):
    """Stable edit-policy vocabulary exposed by the guide command."""

    EDIT_FREELY = "edit freely"
    PROPOSALS_OR_TRANSACTIONS = "through proposals or transactions"
    PRESERVED_BUT_FREEZES_UPDATES = "preserved-but-freezes-updates"
    NEVER_EDIT_BY_HAND = "never edit by hand"


_POLICY_BY_CLASS = {
    OwnershipClass.CANONICAL_VIA_PROPOSALS: EditPolicy.PROPOSALS_OR_TRANSACTIONS,
    OwnershipClass.OPERATOR_OWNED: EditPolicy.EDIT_FREELY,
    OwnershipClass.ADAPTER_MANAGED: EditPolicy.PRESERVED_BUT_FREEZES_UPDATES,
    OwnershipClass.GENERATED: EditPolicy.NEVER_EDIT_BY_HAND,
    OwnershipClass.MACHINE_STATE: EditPolicy.NEVER_EDIT_BY_HAND,
}


@dataclass(frozen=True, slots=True)
class OwnershipEntry:
    """One path-level ownership and edit contract."""

    path: str
    ownership_class: OwnershipClass
    policy: EditPolicy
    details: str
    regenerate_with: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.policy is not _POLICY_BY_CLASS[self.ownership_class]:
            raise ValueError("Ownership class and edit policy do not match")
        if (self.policy is EditPolicy.NEVER_EDIT_BY_HAND) != bool(self.regenerate_with):
            raise ValueError("Never-edit entries alone must name regeneration commands")

    def to_payload(self) -> dict[str, JsonValue]:
        """Return the stable JSON representation for this entry."""

        return {
            "path": self.path,
            "class": self.ownership_class.value,
            "policy": self.policy.value,
            "regenerate_with": list(self.regenerate_with),
            "details": self.details,
        }


@dataclass(frozen=True, slots=True)
class RoutingEntry:
    """One deterministic answer to where a kind of information belongs."""

    kind: str
    destination: str
    via: str

    def to_payload(self) -> dict[str, JsonValue]:
        """Return the stable JSON representation for this route."""

        return {"kind": self.kind, "destination": self.destination, "via": self.via}


@dataclass(frozen=True, slots=True)
class NeverEditEntry:
    """One generated or machine-owned path and its recovery command."""

    path: str
    reason: str
    regenerate_with: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.regenerate_with:
            raise ValueError("Never-edit entries must name at least one regeneration command")

    def to_payload(self) -> dict[str, JsonValue]:
        """Return the stable JSON representation for this entry."""

        return {
            "path": self.path,
            "reason": self.reason,
            "regenerate_with": list(self.regenerate_with),
        }


@dataclass(frozen=True, slots=True)
class GuideDefinition:
    """The single authoritative ownership and placement definition."""

    ownership: tuple[OwnershipEntry, ...]
    routing: tuple[RoutingEntry, ...]
    never_edit: tuple[NeverEditEntry, ...]
    adapter_note: str
    escape_hatch: str

    def __post_init__(self) -> None:
        paths = tuple(entry.path for entry in self.ownership)
        if len(paths) != len(set(paths)):
            raise ValueError("Guide ownership paths must be unique")
        if {entry.ownership_class for entry in self.ownership} != set(OwnershipClass):
            raise ValueError("Guide definition must expose every ownership class")


_AGENT_REGEN = ("workctx agent repair", "workctx agent refresh")

GUIDE = GuideDefinition(
    ownership=(
        OwnershipEntry(
            "00_inbox/",
            OwnershipClass.CANONICAL_VIA_PROPOSALS,
            EditPolicy.PROPOSALS_OR_TRANSACTIONS,
            "Register evidence with workctx inbox add; processing owns manifests, quarantine, "
            "and moves.",
        ),
        OwnershipEntry(
            "01_processed/",
            OwnershipClass.CANONICAL_VIA_PROPOSALS,
            EditPolicy.PROPOSALS_OR_TRANSACTIONS,
            "Preserved originals move here only after successful processing.",
        ),
        OwnershipEntry(
            "02_knowledge/",
            OwnershipClass.CANONICAL_VIA_PROPOSALS,
            EditPolicy.PROPOSALS_OR_TRANSACTIONS,
            "Durable entities, observations, claims, and typed relationships.",
        ),
        OwnershipEntry(
            "03_work/",
            OwnershipClass.CANONICAL_VIA_PROPOSALS,
            EditPolicy.PROPOSALS_OR_TRANSACTIONS,
            "Canonical tasks, investigations, incidents, and plans.",
        ),
        OwnershipEntry(
            "04_views/",
            OwnershipClass.GENERATED,
            EditPolicy.NEVER_EDIT_BY_HAND,
            "Rebuildable views derived from canonical context; never the only copy.",
            ("workctx view rebuild",),
        ),
        OwnershipEntry(
            "05_outbox/",
            OwnershipClass.CANONICAL_VIA_PROPOSALS,
            EditPolicy.PROPOSALS_OR_TRANSACTIONS,
            "Canonical unsent drafts saved through the draft flow.",
        ),
        OwnershipEntry(
            "06_overrides/",
            OwnershipClass.OPERATOR_OWNED,
            EditPolicy.EDIT_FREELY,
            "Author reviewed skill overrides only at skills/<name>/SKILL.md; Work Context "
            "validates but never creates or overwrites them.",
        ),
        OwnershipEntry(
            "90_integrations/",
            OwnershipClass.CANONICAL_VIA_PROPOSALS,
            EditPolicy.PROPOSALS_OR_TRANSACTIONS,
            "Integration records and secret reference names only.",
        ),
        OwnershipEntry(
            "98_state/",
            OwnershipClass.MACHINE_STATE,
            EditPolicy.NEVER_EDIT_BY_HAND,
            "Indexes, locks, caches, adapter manifests, and local runtime state; use the owning "
            "Work Context command for each subtree.",
            ("workctx index rebuild", "workctx agent repair"),
        ),
        OwnershipEntry(
            "99_meta/",
            OwnershipClass.CANONICAL_VIA_PROPOSALS,
            EditPolicy.PROPOSALS_OR_TRANSACTIONS,
            "Policies, templates, migrations, and audit metadata; transactions own the ledger.",
        ),
        OwnershipEntry(
            "context.yaml",
            OwnershipClass.OPERATOR_OWNED,
            EditPolicy.EDIT_FREELY,
            "Context identity, language, boundary, and policy configuration; validate after edits.",
        ),
        OwnershipEntry(
            ".gitignore",
            OwnershipClass.OPERATOR_OWNED,
            EditPolicy.EDIT_FREELY,
            "Operator-owned ignore rules supplied by the context template.",
        ),
        OwnershipEntry(
            "README.md",
            OwnershipClass.OPERATOR_OWNED,
            EditPolicy.EDIT_FREELY,
            "Operator-facing workspace documentation.",
        ),
        OwnershipEntry(
            "instructions.md",
            OwnershipClass.OPERATOR_OWNED,
            EditPolicy.EDIT_FREELY,
            "Optional context personalization merged after the user-level instructions layer.",
        ),
        OwnershipEntry(
            ".agents/skills/",
            OwnershipClass.ADAPTER_MANAGED,
            EditPolicy.PRESERVED_BUT_FREEZES_UPDATES,
            "Canonical packaged skill sources declared under registry.yaml skills; packaged "
            "context files refresh only while their tracked bytes remain pristine.",
        ),
        OwnershipEntry(
            ".agents/skills/registry.yaml custom_skills + .agents/skills/<id>/",
            OwnershipClass.OPERATOR_OWNED,
            EditPolicy.EDIT_FREELY,
            "Sanctioned context-local custom skill registration and source home; Work Context "
            "validates these files, renders them for clients, and never replaces them from the "
            "packaged kit.",
        ),
        OwnershipEntry(
            "AGENTS.md",
            OwnershipClass.ADAPTER_MANAGED,
            EditPolicy.PRESERVED_BUT_FREEZES_UPDATES,
            "The pristine context template may become the Codex bridge; other existing or edited "
            "content remains operator-owned and is preserved.",
        ),
        OwnershipEntry(
            "CLAUDE.md",
            OwnershipClass.ADAPTER_MANAGED,
            EditPolicy.PRESERVED_BUT_FREEZES_UPDATES,
            "Generated only when absent; existing or edited content remains operator-owned and "
            "is preserved.",
        ),
        OwnershipEntry(
            "GEMINI.md",
            OwnershipClass.ADAPTER_MANAGED,
            EditPolicy.PRESERVED_BUT_FREEZES_UPDATES,
            "Generated only when absent; existing or edited content remains operator-owned and "
            "is preserved.",
        ),
        OwnershipEntry(
            ".codex/",
            OwnershipClass.ADAPTER_MANAGED,
            EditPolicy.PRESERVED_BUT_FREEZES_UPDATES,
            "Work Context manages only config.toml when it created that file; an existing config "
            "and unrelated files are preserved.",
        ),
        OwnershipEntry(
            ".mcp.json",
            OwnershipClass.ADAPTER_MANAGED,
            EditPolicy.PRESERVED_BUT_FREEZES_UPDATES,
            "Claude MCP configuration is generated only when absent; an existing file remains "
            "operator-owned and is never merged.",
        ),
        OwnershipEntry(
            ".claude/skills/",
            OwnershipClass.GENERATED,
            EditPolicy.NEVER_EDIT_BY_HAND,
            "Claude skill projections rendered from canonical .agents/skills/ sources.",
            _AGENT_REGEN,
        ),
        OwnershipEntry(
            ".gemini/skills/",
            OwnershipClass.GENERATED,
            EditPolicy.NEVER_EDIT_BY_HAND,
            "Gemini skill projections rendered from canonical .agents/skills/ sources.",
            _AGENT_REGEN,
        ),
        OwnershipEntry(
            ".gemini/settings.json",
            OwnershipClass.ADAPTER_MANAGED,
            EditPolicy.PRESERVED_BUT_FREEZES_UPDATES,
            "Gemini MCP configuration is generated only when absent; an existing file remains "
            "operator-owned and is never merged.",
        ),
    ),
    routing=(
        RoutingEntry(
            "person fact",
            "person entity under 02_knowledge/",
            "proposal or transaction",
        ),
        RoutingEntry(
            "access or process fact",
            "integration entity in 90_integrations/ or system entity in 02_knowledge/",
            "proposal or transaction",
        ),
        RoutingEntry(
            "standing operator preference",
            "context instructions.md; user-level instructions.md for all contexts",
            "operator-reviewed suggestion",
        ),
        RoutingEntry("evidence", "00_inbox/", "workctx inbox add"),
        RoutingEntry("task or work item", "03_work/", "proposal or transaction"),
        RoutingEntry("outbound draft", "05_outbox/", "draft flow"),
        RoutingEntry(
            "workflow customization",
            "06_overrides/skills/<name>/SKILL.md",
            "operator-owned override flow",
        ),
        RoutingEntry(
            "custom agent workflow",
            "custom_skills in .agents/skills/registry.yaml + .agents/skills/<id>/",
            "operator-reviewed custom skill registration",
        ),
        RoutingEntry(
            "secret material",
            "nowhere in the context; keep reference names only",
            "the configured secret reference name",
        ),
    ),
    never_edit=(
        NeverEditEntry(
            "04_views/",
            "generated operational views",
            ("workctx view rebuild",),
        ),
        NeverEditEntry(
            ".claude/skills/ and .gemini/skills/",
            "generated agent skill projections",
            _AGENT_REGEN,
        ),
        NeverEditEntry(
            "manifest-managed AGENTS.md, CLAUDE.md, GEMINI.md, and MCP configuration",
            "authenticated adapter outputs; existing operator-owned files are excluded",
            _AGENT_REGEN,
        ),
        NeverEditEntry(
            "98_state/",
            "machine state owned by Work Context commands",
            ("workctx index rebuild", "workctx agent repair"),
        ),
    ),
    adapter_note=(
        "Adapter-managed files use manifest or adoption hashes. Existing operator-owned bridges "
        "and MCP configuration stay untouched; hand-editing a managed file is preserved but "
        "freezes updates and blocks repair or refresh. Registered custom_skills and their "
        ".agents/skills/<id>/ sources are operator-owned and do not freeze packaged refreshes."
    ),
    escape_hatch=(
        "If a generated file seems to require a manual edit, stop, run `workctx agent repair` "
        "or `workctx agent refresh`, or ask the operator — editing it directly freezes it out "
        "of updates and blocks refresh."
    ),
)


def guide_payload(root: Path) -> dict[str, JsonValue]:
    """Render the authoritative definition as a stable JSON-compatible result."""

    return {
        "schema_version": GUIDE_SCHEMA_VERSION,
        "root": str(root),
        "ownership": cast(JsonValue, [entry.to_payload() for entry in GUIDE.ownership]),
        "routing": cast(JsonValue, [entry.to_payload() for entry in GUIDE.routing]),
        "never_edit": cast(JsonValue, [entry.to_payload() for entry in GUIDE.never_edit]),
        "adapter_note": GUIDE.adapter_note,
        "escape_hatch": GUIDE.escape_hatch,
    }


def render_guide(*, context_id: str) -> RenderableType:
    """Render the authoritative definition as compact human-readable tables."""

    ownership = Table(box=None, pad_edge=False, collapse_padding=True)
    ownership.add_column("Class", style="magenta", width=23, no_wrap=True)
    ownership.add_column("Edit policy / paths", overflow="fold")
    for ownership_class in OwnershipClass:
        entries = tuple(
            entry for entry in GUIDE.ownership if entry.ownership_class is ownership_class
        )
        paths = ", ".join(entry.path for entry in entries)
        policy = _POLICY_BY_CLASS[ownership_class]
        policy_text = policy.value
        if policy is EditPolicy.NEVER_EDIT_BY_HAND:
            policy_text += "; use the commands below"
        ownership.add_row(ownership_class.value, f"{policy_text}\n{paths}")

    routing = Table(box=None, pad_edge=False, collapse_padding=True)
    routing.add_column("Information", style="cyan", width=25, overflow="fold")
    routing.add_column("Destination and route", overflow="fold")
    for route in GUIDE.routing:
        routing.add_row(route.kind, f"{route.destination}; via {route.via}")

    never_edit = Table(box=None, pad_edge=False, collapse_padding=True)
    never_edit.add_column("Path", style="cyan", ratio=3, overflow="fold")
    never_edit.add_column("Regenerate with", ratio=2, overflow="fold")
    for protected_path in GUIDE.never_edit:
        commands = " or ".join(protected_path.regenerate_with)
        never_edit.add_row(protected_path.path, commands)

    return Group(
        Text(f"File placement and ownership — {context_id}", style="bold"),
        Text("Ownership", style="bold"),
        ownership,
        Text(GUIDE.adapter_note),
        Text(),
        Text("Where does it go?", style="bold"),
        routing,
        Text(),
        Text("Never edit by hand", style="bold"),
        never_edit,
        Text(GUIDE.escape_hatch, style="bold"),
    )


__all__ = [
    "GUIDE",
    "GUIDE_SCHEMA_VERSION",
    "EditPolicy",
    "GuideDefinition",
    "NeverEditEntry",
    "OwnershipClass",
    "OwnershipEntry",
    "RoutingEntry",
    "guide_payload",
    "render_guide",
]

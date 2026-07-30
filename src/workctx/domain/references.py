from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import quote, unquote, urlparse

from workctx.domain.ids import ObservationId
from workctx.domain.vocabulary import EntityType

_CONTEXT_ID_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_ENTITY_TYPE_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_REPO_ID_PATTERN = re.compile(r"^[a-z0-9]+(?:[._-][a-z0-9]+)*$")
_COMMIT_PATTERN = re.compile(r"^[0-9a-fA-F]{7,64}$")
_ARTIFACT_DIGEST_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_ARTIFACT_URI_PATTERN = re.compile(r"^artifact://sha256/(?P<digest>[0-9a-f]{64})$")
_REPO_LINE_FRAGMENT_PATTERN = re.compile(r"^L(?P<start>[1-9][0-9]*)-L(?P<end>[1-9][0-9]*)$")
_GENERIC_URI_PATTERN = re.compile(r"^[a-z][a-z0-9+.-]*://.+$")
_WINDOWS_ABSOLUTE_PATH_PATTERN = re.compile(r"^[A-Za-z]:[\\/]")
_OBSERVATION_FRAGMENT_PATTERN = re.compile(r"^OBS-[0-9]{3}$")
_INVALID_PERCENT_ESCAPE_PATTERN = re.compile(r"%(?![0-9a-fA-F]{2})")

_CONTEXT_PATTERN_HELP = "context IDs use lowercase ASCII letters, digits, and single hyphens"


@dataclass(frozen=True, slots=True)
class WorkctxUri:
    context_id: str
    entity_type: str
    entity_id: str

    def __post_init__(self) -> None:
        if not _CONTEXT_ID_PATTERN.fullmatch(self.context_id):
            raise ValueError(f"Invalid context ID: {_CONTEXT_PATTERN_HELP}")
        if not _ENTITY_TYPE_PATTERN.fullmatch(self.entity_type):
            raise ValueError("Entity types use lowercase ASCII letters, digits, and single hyphens")
        if (
            not self.entity_id
            or self.entity_id in {".", ".."}
            or "/" in self.entity_id
            or "\\" in self.entity_id
        ):
            raise ValueError("Entity ID is required and cannot be a path traversal segment")

    @classmethod
    def parse(cls, value: str) -> WorkctxUri:
        parsed = urlparse(value)
        if parsed.scheme != "workctx":
            raise ValueError("URI scheme must be 'workctx'")
        if parsed.params or parsed.query or "?" in value:
            raise ValueError("Work Context URIs cannot contain params, query, or fragment")
        if parsed.fragment or "#" in value:
            raise ValueError(
                "Work Context URIs cannot contain a literal '#' fragment; encode '#' as '%23' "
                "or use normalize_workctx_uri()"
            )

        parts = parsed.path.split("/")
        if len(parts) != 3 or parts[0] or not parts[1] or not parts[2]:
            raise ValueError(
                "Work Context URI must contain exactly one entity type and one entity ID "
                "without empty path segments"
            )
        entity_type, encoded_entity_id = parts[1:]
        _validate_percent_encoding(encoded_entity_id, "Entity ID")
        return cls(
            context_id=parsed.netloc,
            entity_type=entity_type,
            entity_id=unquote(encoded_entity_id, errors="strict"),
        )

    def __str__(self) -> str:
        encoded_id = quote(self.entity_id, safe="-._~")
        return f"workctx://{self.context_id}/{self.entity_type}/{encoded_id}"

    def require_context(self, active_context_id: str) -> None:
        if self.context_id != active_context_id:
            raise ValueError(
                f"Reference belongs to context '{self.context_id}', not '{active_context_id}'"
            )


def normalize_workctx_uri(value: str) -> str:
    """Return a canonical URI, encoding an authored observation ``#OBS-NNN`` separator."""

    parsed = urlparse(value)
    if not parsed.fragment:
        uri = WorkctxUri.parse(value)
        if uri.entity_type == "observation":
            ObservationId.parse(uri.entity_id)
        return str(uri)

    if parsed.params or parsed.query:
        return str(WorkctxUri.parse(value))
    if parsed.scheme != "workctx" or not _OBSERVATION_FRAGMENT_PATTERN.fullmatch(parsed.fragment):
        raise ValueError("Only a literal '#OBS-NNN' observation separator can be normalized")

    parts = parsed.path.split("/")
    if len(parts) != 3 or parts[0] or parts[1] != "observation" or not parts[2]:
        raise ValueError("A literal '#OBS-NNN' separator is valid only for an observation URI")

    candidate = value.partition("#")[0] + "%23" + parsed.fragment
    uri = WorkctxUri.parse(candidate)
    ObservationId.parse(uri.entity_id)
    return str(uri)


def validate_repo_id(value: str) -> str:
    if not isinstance(value, str) or not _REPO_ID_PATTERN.fullmatch(value):
        raise ValueError("Repository IDs use lowercase ASCII segments separated by '.', '_' or '-'")
    return value


def _validate_percent_encoding(value: str, label: str) -> None:
    if _INVALID_PERCENT_ESCAPE_PATTERN.search(value):
        raise ValueError(f"{label} contains a malformed percent escape")


def validate_commit_sha(value: str) -> str:
    if not isinstance(value, str) or not _COMMIT_PATTERN.fullmatch(value):
        raise ValueError("Repository commit must be a 7-64 character hexadecimal SHA")
    return value


def validate_repo_path(value: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("Repository path is required")
    if (
        value.startswith(("/", "\\"))
        or _WINDOWS_ABSOLUTE_PATH_PATTERN.match(value)
        or "\\" in value
    ):
        raise ValueError("Repository path must be relative and use forward slashes")
    if any(part in {"", ".", ".."} for part in value.split("/")):
        raise ValueError("Repository path cannot contain empty or traversal segments")
    if "\x00" in value:
        raise ValueError("Repository path cannot contain a null byte")
    return value


@dataclass(frozen=True, slots=True)
class ArtifactReference:
    digest: str

    def __post_init__(self) -> None:
        if not isinstance(self.digest, str) or not _ARTIFACT_DIGEST_PATTERN.fullmatch(self.digest):
            raise ValueError("Artifact digest must contain exactly 64 lowercase hexadecimal digits")

    @classmethod
    def parse(cls, value: str) -> ArtifactReference:
        if not isinstance(value, str):
            raise ValueError("Artifact reference must be a string")
        match = _ARTIFACT_URI_PATTERN.fullmatch(value)
        if match is None:
            raise ValueError("Artifact reference must use artifact://sha256/<64-lowercase-hex>")
        return cls(digest=match.group("digest"))

    def __str__(self) -> str:
        return f"artifact://sha256/{self.digest}"


@dataclass(frozen=True, slots=True)
class RepoReference:
    repo_id: str
    commit: str
    path: str
    start_line: int
    end_line: int

    def __post_init__(self) -> None:
        validate_repo_id(self.repo_id)
        validate_commit_sha(self.commit)
        validate_repo_path(self.path)
        if type(self.start_line) is not int or self.start_line < 1:
            raise ValueError("Repository start line must be an integer greater than or equal to 1")
        if type(self.end_line) is not int or self.end_line < self.start_line:
            raise ValueError("Repository end line must be greater than or equal to start line")

    @classmethod
    def parse(cls, value: str) -> RepoReference:
        if not isinstance(value, str) or not value.startswith("repo://"):
            raise ValueError("Repository reference must use the 'repo' URI scheme")

        parsed = urlparse(value)
        if parsed.scheme != "repo" or parsed.params or parsed.query:
            raise ValueError("Repository references cannot contain params or query values")
        if "@" not in parsed.netloc:
            raise ValueError("Repository reference must include '@<commit>'")
        repo_id, commit = parsed.netloc.rsplit("@", 1)
        line_match = _REPO_LINE_FRAGMENT_PATTERN.fullmatch(parsed.fragment)
        if line_match is None:
            raise ValueError("Repository reference must end with '#L<start>-L<end>'")
        if not parsed.path.startswith("/") or parsed.path == "/":
            raise ValueError("Repository reference must include a relative path")

        encoded_path = parsed.path[1:]
        _validate_percent_encoding(encoded_path, "Repository path")
        path = unquote(encoded_path, errors="strict")
        reference = cls(
            repo_id=repo_id,
            commit=commit,
            path=path,
            start_line=int(line_match.group("start")),
            end_line=int(line_match.group("end")),
        )
        if str(reference) != value:
            raise ValueError("Repository reference must use its canonical URI encoding")
        return reference

    def __str__(self) -> str:
        encoded_path = quote(self.path, safe="/-._~")
        return (
            f"repo://{self.repo_id}@{self.commit}/{encoded_path}"
            f"#L{self.start_line}-L{self.end_line}"
        )


ArtifactUri = ArtifactReference
RepoUri = RepoReference

type SourceReference = ArtifactReference | RepoReference
type DurableReference = WorkctxUri | ArtifactReference | RepoReference | str


def parse_source_reference(value: str) -> SourceReference:
    if value.startswith("artifact://"):
        return ArtifactReference.parse(value)
    if value.startswith("repo://"):
        return RepoReference.parse(value)
    raise ValueError("Source reference must use the 'artifact' or 'repo' URI scheme")


def _is_absolute_machine_path(value: str) -> bool:
    return value.startswith(("/", "\\")) or bool(_WINDOWS_ABSOLUTE_PATH_PATTERN.match(value))


def validate_workctx_entity_uri(value: str) -> WorkctxUri:
    """Parse a canonical entity URI and require the D-018 entity-type vocabulary."""

    uri = WorkctxUri.parse(value)
    try:
        EntityType(uri.entity_type)
    except ValueError as error:
        raise ValueError(f"Unknown Work Context entity type: {uri.entity_type!r}") from error
    if str(uri) != value:
        raise ValueError("Work Context entity reference must use its canonical URI encoding")
    return uri


def parse_durable_reference(value: str) -> DurableReference:
    """Parse known durable references and validate placeholder external URIs."""

    if not isinstance(value, str) or not value:
        raise ValueError("Durable reference must be a non-empty URI string")
    if _is_absolute_machine_path(value):
        raise ValueError("Absolute machine paths are not durable references")

    separator = value.find("://")
    if separator > 0 and value[:separator] != value[:separator].lower():
        raise ValueError("Durable reference URI schemes must use canonical lowercase")

    scheme = urlparse(value).scheme.lower()
    if scheme == "file":
        raise ValueError("file:// URIs are machine-specific and cannot be durable references")
    if scheme == "workctx":
        return validate_workctx_entity_uri(value)
    if scheme == "artifact":
        return ArtifactReference.parse(value)
    if scheme == "repo":
        return RepoReference.parse(value)
    if not _GENERIC_URI_PATTERN.fullmatch(value):
        raise ValueError("Durable reference must use a '<scheme>://<target>' URI")
    return value


def validate_durable_reference(value: str) -> str:
    return str(parse_durable_reference(value))

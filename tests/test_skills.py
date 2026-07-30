from pathlib import Path

import yaml
from jsonschema import Draft202012Validator

ROOT = Path(__file__).parents[1]
SKILLS_ROOT = ROOT / ".agents" / "skills"


def _frontmatter(path: Path) -> dict[str, object]:
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n"), f"Missing frontmatter: {path}"
    _, raw, _ = text.split("---", maxsplit=2)
    parsed = yaml.safe_load(raw)
    assert isinstance(parsed, dict)
    return parsed


def test_canonical_skill_frontmatter_is_valid_and_unique() -> None:
    schema = yaml.safe_load(
        (ROOT / "schemas" / "skill-frontmatter.schema.json").read_text(encoding="utf-8")
    )
    validator = Draft202012Validator(schema)
    names: set[str] = set()

    skill_paths = sorted(SKILLS_ROOT.glob("*/SKILL.md"))
    assert skill_paths

    for path in skill_paths:
        metadata = _frontmatter(path)
        validator.validate(metadata)
        name = str(metadata["name"])
        assert name == path.parent.name
        assert name not in names
        names.add(name)

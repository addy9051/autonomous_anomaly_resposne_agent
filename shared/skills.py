"""
Procedural memory: a versioned "book of reference" of skills.

A skill is a ``skills/<name>/SKILL.md`` file (legacy flat ``skills/*.md`` also loads) with YAML front matter
describing when it applies and a body telling the agents how to behave for that trigger: what to gather, which actions
are allowed, when to escalate, and how to report. The router picks skills deterministically from the alert; the
rendered text is injected into agent prompts. Skills are plain files reviewed in git.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from shared.utils import get_logger

logger = get_logger("skills")

DEFAULT_SKILLS_DIR = Path(__file__).resolve().parent.parent / "skills"


@dataclass(frozen=True)
class Skill:
    name: str
    version: str
    priority: int
    anomaly_types: frozenset[str]
    severities: frozenset[str]
    services: frozenset[str]
    body: str
    path: Path | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    def matches(self, anomaly_type: str, severity: str, services: list[str]) -> bool:
        if self.anomaly_types and anomaly_type not in self.anomaly_types:
            return False
        if self.severities and severity not in self.severities:
            return False
        return not (self.services and not self.services.intersection(services))


def parse_skill(text: str, path: Path | None = None) -> Skill:
    """Parse a markdown skill with ``---`` YAML front matter."""
    if not text.startswith("---"):
        raise ValueError(f"skill {path} is missing front matter")
    _, front, body = text.split("---", 2)
    meta = yaml.safe_load(front) or {}
    if "name" not in meta:
        raise ValueError(f"skill {path} has no name")
    # SKILL.md keeps routing data under `metadata`; flat legacy files keep it at top level.
    routing = {**meta, **(meta.get("metadata") or {})}
    trig = routing.get("triggers") or {}
    return Skill(
        name=str(meta["name"]),
        version=str(routing.get("version", "1")),
        priority=int(routing.get("priority", 50)),
        anomaly_types=frozenset(trig.get("anomaly_types") or []),
        severities=frozenset(trig.get("severities") or []),
        services=frozenset(trig.get("services") or []),
        body=body.strip(),
        path=path,
        meta=meta,
    )


class SkillRouter:
    """Loads skills from disk and selects those that apply to an alert."""

    def __init__(self, skills_dir: Path | str | None = None) -> None:
        self.skills_dir = Path(skills_dir) if skills_dir else DEFAULT_SKILLS_DIR
        self.skills: list[Skill] = self._load()

    def _load(self) -> list[Skill]:
        skills: list[Skill] = []
        if not self.skills_dir.exists():
            logger.warning("skills_dir_missing", path=str(self.skills_dir))
            return skills
        paths = sorted(self.skills_dir.glob("*/SKILL.md")) + sorted(self.skills_dir.glob("*.md"))
        for path in paths:
            try:
                skills.append(parse_skill(path.read_text(encoding="utf-8"), path))
            except Exception as exc:
                # A malformed skill must be loud but must not take down alert handling.
                logger.error("skill_load_failed", path=str(path), error=str(exc))
        return skills

    def select(self, anomaly_type: str, severity: str, services: list[str]) -> list[Skill]:
        """Matching skills, most specific/highest priority first. Always includes baseline skills."""
        matched = [s for s in self.skills if s.matches(anomaly_type, severity, services)]
        return sorted(matched, key=lambda s: (-s.priority, s.name))

    @staticmethod
    def render(skills: list[Skill]) -> str:
        if not skills:
            return "No procedural skill matched this trigger; fall back to general SRE judgement and state so."
        return "\n\n".join(f"### Skill: {s.name} (v{s.version})\n{s.body}" for s in skills)


_router: SkillRouter | None = None


def get_skill_router() -> SkillRouter:
    global _router
    if _router is None:
        _router = SkillRouter()
    return _router

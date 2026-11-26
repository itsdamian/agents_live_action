"""Seats, lineups and the rules for custom agents (plan section 4.2, D17, D21).

A *seat* is one place on an assembly line: a built-in station (``pm`` / ``swe`` / ``qa``)
or a custom agent the user added. A *lineup* is the ordered seats of one pipeline. The
default lineup is the three built-ins, which is exactly the pipeline that existed before
custom agents.

The validators here are pure functions returning a list of errors
(``{"field": ..., "message": <Traditional Chinese>}``), empty when the input is fine, so the
REST layer can put them into a 422 ``errors`` list as they are.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Collection, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass

from core.llm_client import ANTHROPIC_MODELS

KINDS = ("make", "check")
LOOKS = ("cap", "glasses", "headset", "bow", "antenna", "beret")

# Lower-case letter first, then 1-23 of [a-z0-9-]. ``\Z`` (not ``$``): no trailing newline.
AGENT_ID_RE = re.compile(r"^[a-z][a-z0-9-]{1,23}\Z")

# Built-in agents, LangGraph node names of the pipeline graph and words used in REST paths.
RESERVED_IDS = frozenset({
    "pm", "swe", "qa", "devops",
    "intake", "gate_enter", "gate_wait", "gate_exit", "rework", "done", "fail",
    "start", "end", "main", "agents", "pipelines", "history", "new", "export", "import",
})

DISPLAY_NAME_MAX = 14
DUTY_MAX = 24
PROMPT_MAX = 2000
MAX_CUSTOM_AGENTS = 16
MAX_SEATS = 8
MAX_PIPELINES = 3

DEFAULT_PIPELINE_ID = "main"
DEFAULT_PIPELINE_NAME = "主線"


@dataclass(frozen=True)
class Seat:
    id: str
    kind: str  # "make" | "check"
    builtin: bool
    display_name: str
    duty: str
    look: str

    def snapshot(self) -> dict:
        """The seat as kept in ``task_runs.seats`` (``SeatSnapshot``)."""

        return {"id": self.id, "display_name": self.display_name, "look": self.look, "kind": self.kind}


@dataclass(frozen=True)
class Lineup:
    pipeline_id: str
    seats: tuple[Seat, ...]

    @property
    def ids(self) -> tuple[str, ...]:
        return tuple(seat.id for seat in self.seats)

    def signature(self) -> tuple[tuple[str, str], ...]:
        """What the graph's shape depends on (compiled graphs are cached by it, plan 5.1)."""

        return tuple((seat.id, seat.kind) for seat in self.seats)

    def is_default(self) -> bool:
        return self.signature() == DEFAULT_LINEUP.signature()

    def seat(self, seat_id: str) -> Seat:
        for seat in self.seats:
            if seat.id == seat_id:
                return seat
        raise KeyError(seat_id)

    def index(self, seat_id: str) -> int:
        return self.ids.index(seat_id)


BUILTIN_SEATS: tuple[Seat, ...] = (
    Seat("pm", "make", True, "PM", "把需求拆成規格", "glasses"),
    Seat("swe", "make", True, "SWE", "照規格寫程式", "headset"),
    Seat("qa", "check", True, "QA", "測試並決定過不過", "cap"),
)

DEFAULT_LINEUP = Lineup(DEFAULT_PIPELINE_ID, BUILTIN_SEATS)


@dataclass(frozen=True)
class AgentView:
    """An agent as the frontend sees it (plan section 6.3, FROZEN)."""

    id: str
    display_name: str
    duty: str
    kind: str
    look: str
    builtin: bool
    llm: str | dict | None
    effective_llm: dict

    @classmethod
    def from_seat(cls, seat: Seat, *, llm, effective_llm: dict) -> AgentView:
        return cls(seat.id, seat.display_name, seat.duty, seat.kind, seat.look, seat.builtin,
                   deepcopy(llm), deepcopy(effective_llm))

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "display_name": self.display_name,
            "duty": self.duty,
            "kind": self.kind,
            "look": self.look,
            "builtin": self.builtin,
            "llm": deepcopy(self.llm),
            "effective_llm": deepcopy(self.effective_llm),
        }


# -- validators ---------------------------------------------------------------------------------


def _error(field: str, message: str) -> dict:
    return {"field": field, "message": message}


def validate_agent_id(agent_id: str) -> list[dict]:
    if not isinstance(agent_id, str) or not AGENT_ID_RE.match(agent_id):
        return [_error("id", "id 必須是 2–24 個字元，小寫英文字母開頭，只能含小寫英數字與 -")]
    if agent_id in RESERVED_IDS:
        return [_error("id", f"id「{agent_id}」是保留字，請換一個")]
    return []


def _has_control_or_format(text: str) -> bool:
    return any(unicodedata.category(ch) in ("Cc", "Cf") for ch in text)


def _text_errors(field: str, label: str, value, maximum: int) -> list[dict]:
    if not isinstance(value, str) or not value.strip():
        return [_error(field, f"{label}不能是空的")]
    if len(value) > maximum:
        return [_error(field, f"{label}最多 {maximum} 個字")]
    return []


def validate_agent_fields(
    *, display_name, duty, prompt, kind, look, agent_id: str | None = None
) -> list[dict]:
    """Errors of a custom agent's editable fields (and of ``agent_id`` when one is given)."""

    errors: list[dict] = []
    if agent_id is not None:
        errors += validate_agent_id(agent_id)
    name_errors = _text_errors("display_name", "名字", display_name, DISPLAY_NAME_MAX)
    if not name_errors and _has_control_or_format(display_name):
        name_errors = [_error("display_name", "名字不能含控制字元或格式字元")]
    errors += name_errors
    errors += _text_errors("duty", "職責", duty, DUTY_MAX)
    errors += _text_errors("prompt", "prompt ", prompt, PROMPT_MAX)
    if kind not in KINDS:
        errors.append(_error("kind", "kind 只能是 make 或 check"))
    if look not in LOOKS:
        errors.append(_error("look", f"造型只能是 {'、'.join(LOOKS)} 其中之一"))
    return errors


def derive_agent_id(display_name: str, existing: Collection[str]) -> str:
    """An id built from ``display_name``; ``agent-NN`` when that gives nothing usable."""

    slug = re.sub(r"[^a-z0-9]+", "-", display_name.lower()).strip("-")
    slug = slug[:24].rstrip("-")
    if not validate_agent_id(slug) and slug not in existing:
        return slug
    n = 1
    while f"agent-{n:02d}" in existing:
        n += 1
    return f"agent-{n:02d}"


def _kind_of(value) -> str | None:
    return value.kind if isinstance(value, Seat) else value


def validate_lineup(seat_ids: Sequence[str], roster: Mapping[str, object]) -> list[dict]:
    """Errors of a pipeline's seats. ``roster`` maps every agent id to its kind (or its Seat)."""

    seat_ids = list(seat_ids)
    errors: list[dict] = []
    if not 1 <= len(seat_ids) <= MAX_SEATS:
        errors.append(_error("seats", f"一條線要有 1–{MAX_SEATS} 個座位"))
    duplicates = sorted({s for s in seat_ids if seat_ids.count(s) > 1})
    if duplicates:
        errors.append(_error("seats", f"座位重複：{'、'.join(duplicates)}"))
    unknown = [s for s in seat_ids if s not in roster]
    if unknown:
        errors.append(_error("seats", f"沒有這個 agent：{'、'.join(unknown)}"))
    kinds = [_kind_of(roster[s]) for s in seat_ids if s in roster]
    if seat_ids and "make" not in kinds:
        errors.append(_error("seats", "至少要有一個 make 型的座位"))
    if seat_ids and seat_ids[0] in roster and _kind_of(roster[seat_ids[0]]) == "check":
        errors.append(_error("seats", "第一席不能是 check 型（它沒有東西可以檢查）"))
    return errors


class InvalidLineup(ValueError):
    """A lineup that can not run; ``errors`` are ``{"field", "message"}`` dicts."""

    def __init__(self, errors: list[dict]) -> None:
        self.errors = errors
        super().__init__("invalid lineup: " + "; ".join(e["message"] for e in errors))


def lineup_errors(lineup: Lineup) -> list[dict]:
    """Errors of a ``Lineup`` object: its seat ids and kinds, then ``validate_lineup``'s rules."""

    builtins = {seat.id: seat for seat in BUILTIN_SEATS}
    errors: list[dict] = []
    for seat in lineup.seats:
        if seat.builtin:
            real = builtins.get(seat.id)
            if real is None or real.kind != seat.kind:
                errors.append(_error("seats", f"「{seat.id}」不是內建的座位"))
            continue
        errors += validate_agent_id(seat.id)
        if seat.kind not in KINDS:
            errors.append(_error("kind", "kind 只能是 make 或 check"))
    errors += validate_lineup(lineup.ids, {seat.id: seat for seat in lineup.seats})
    return errors


def validate_llm_choice(value, *, allow_null: bool = True) -> list[dict]:
    """Errors of an ``llm`` setting (plan D21).

    Accepted: ``None`` (follow the project default; only where ``allow_null``), ``"mock"``,
    ``{"provider": "mock"}`` and ``{"provider": "anthropic", "model": <whitelisted>}``.
    ``api_key_env`` (or any other key) is refused: the provider decides the env var.
    """

    if value is None:
        return [] if allow_null else [_error("llm", "llm 不能是空的")]
    if value == "mock":
        return []
    if not isinstance(value, dict):
        return [_error("llm", "llm 只能是 \"mock\" 或 {\"provider\", \"model\"}")]
    if "api_key_env" in value:
        return [_error("llm", "不接受 api_key_env：金鑰的環境變數由 provider 決定")]
    extra = sorted(set(value) - {"provider", "model"})
    if extra:
        return [_error("llm", f"llm 不接受這些欄位：{'、'.join(map(str, extra))}")]
    provider = value.get("provider")
    if provider == "mock":
        return []
    if provider != "anthropic":
        return [_error("llm", "provider 只能是 mock 或 anthropic")]
    if value.get("model") not in ANTHROPIC_MODELS:
        return [_error("llm", f"model 只能是 {'、'.join(ANTHROPIC_MODELS)} 其中之一")]
    return []

"""Seat / Lineup model, id rules and validators (Task 17, plan section 4.2, D21)."""

from __future__ import annotations

import pytest

from core.llm_client import ANTHROPIC_MODELS
from core.usage_tracker import MODEL_PRICES
from models.lineup import (
    AGENT_ID_RE,
    BUILTIN_SEATS,
    DEFAULT_LINEUP,
    LOOKS,
    MAX_SEATS,
    RESERVED_IDS,
    AgentView,
    Lineup,
    Seat,
    derive_agent_id,
    validate_agent_fields,
    validate_agent_id,
    validate_lineup,
    validate_llm_choice,
)


def _fields(errors):
    return [e["field"] for e in errors]


def designer(**overrides) -> Seat:
    base = dict(id="designer", kind="make", builtin=False, display_name="設計師", duty="畫介面草圖", look="beret")
    base.update(overrides)
    return Seat(**base)


# -- the default lineup --------------------------------------------------------------------------


def test_builtin_seats_are_pm_swe_qa_as_in_the_plan():
    assert [(s.id, s.display_name, s.duty, s.kind, s.look, s.builtin) for s in BUILTIN_SEATS] == [
        ("pm", "PM", "把需求拆成規格", "make", "glasses", True),
        ("swe", "SWE", "照規格寫程式", "make", "headset", True),
        ("qa", "QA", "測試並決定過不過", "check", "cap", True),
    ]


def test_default_lineup_is_the_main_pipeline_with_the_three_builtins():
    assert DEFAULT_LINEUP.pipeline_id == "main"
    assert DEFAULT_LINEUP.seats == BUILTIN_SEATS
    assert DEFAULT_LINEUP.ids == ("pm", "swe", "qa")
    assert DEFAULT_LINEUP.signature() == (("pm", "make"), ("swe", "make"), ("qa", "check"))
    assert DEFAULT_LINEUP.is_default()


def test_signature_follows_ids_and_kinds_only():
    a = Lineup("main", (BUILTIN_SEATS[0], designer(), *BUILTIN_SEATS[1:]))
    b = Lineup("main", (BUILTIN_SEATS[0], designer(display_name="別名", look="cap"), *BUILTIN_SEATS[1:]))
    assert a.signature() == b.signature()
    assert a.signature() != DEFAULT_LINEUP.signature()
    assert not a.is_default()


def test_looks_are_the_six_avatar_looks_of_the_frontend():
    assert LOOKS == ("cap", "glasses", "headset", "bow", "antenna", "beret")


# -- id rules ------------------------------------------------------------------------------------


@pytest.mark.parametrize("agent_id", ["designer", "security", "ux-writer", "a1", "q" + "a" * 23])
def test_valid_ids(agent_id):
    assert AGENT_ID_RE.match(agent_id)
    assert validate_agent_id(agent_id) == []


@pytest.mark.parametrize(
    "agent_id",
    [
        "",  # empty
        "a",  # too short
        "q" + "a" * 24,  # 25 chars, too long
        "Designer",  # upper case
        "1designer",  # starts with a digit
        "-designer",  # starts with a dash
        "design_er",  # underscore
        "design er",  # space
        "設計師",  # non-ascii
        "designer\n",  # trailing newline must not sneak past `$`
        "de:sign",  # LangGraph reserves ':' in node names
        "de|sign",  # and '|'
        "../etc",  # path traversal (log and deliverable file names use the id)
    ],
)
def test_invalid_ids(agent_id):
    assert validate_agent_id(agent_id) != []


@pytest.mark.parametrize(
    "agent_id",
    ["pm", "swe", "qa", "devops", "intake", "gate_enter", "gate_wait", "rework", "done", "fail",
     "start", "end", "main", "agents", "pipelines", "history", "new", "export", "import"],
)
def test_reserved_ids_are_refused(agent_id):
    errors = validate_agent_id(agent_id)
    assert errors, agent_id
    if AGENT_ID_RE.match(agent_id):
        assert "保留" in errors[0]["message"]


def test_reserved_ids_cover_every_graph_node_name():
    for node in ("intake", "gate_enter", "gate_wait", "gate_exit", "rework", "done", "fail"):
        assert node in RESERVED_IDS


# -- agent fields --------------------------------------------------------------------------------

GOOD = dict(display_name="設計師", duty="畫介面草圖", prompt="你是介面設計師", kind="make", look="beret")


def test_good_fields_have_no_errors():
    assert validate_agent_fields(**GOOD) == []


@pytest.mark.parametrize(
    "display_name",
    ["", "設" * 15, "設計\u0000師", "設計\n師", "設計​師", "設計‮師", "\u0007", "設計­師"],
)
def test_display_name_length_and_control_or_format_characters(display_name):
    assert _fields(validate_agent_fields(**{**GOOD, "display_name": display_name})) == ["display_name"]


def test_display_name_of_fourteen_characters_is_fine():
    assert validate_agent_fields(**{**GOOD, "display_name": "設" * 14}) == []


@pytest.mark.parametrize(("field", "value"), [
    ("duty", ""), ("duty", "職" * 25), ("prompt", ""), ("prompt", "p" * 2001),
    ("kind", "review"), ("kind", ""), ("look", "hat"), ("look", ""),
])
def test_other_fields(field, value):
    assert _fields(validate_agent_fields(**{**GOOD, field: value})) == [field]


def test_limits_at_the_boundary_are_accepted():
    assert validate_agent_fields(**{**GOOD, "duty": "職" * 24, "prompt": "p" * 2000}) == []


def test_every_bad_field_is_reported_at_once():
    errors = validate_agent_fields(display_name="", duty="", prompt="", kind="x", look="y")
    assert _fields(errors) == ["display_name", "duty", "prompt", "kind", "look"]
    assert all(isinstance(e["message"], str) and e["message"] for e in errors)


def test_an_explicit_id_is_checked_with_the_fields():
    assert _fields(validate_agent_fields(**GOOD, agent_id="Bad Id")) == ["id"]
    assert validate_agent_fields(**GOOD, agent_id="designer") == []


# -- deriving an id ------------------------------------------------------------------------------


@pytest.mark.parametrize(("display_name", "existing", "expected"), [
    ("Designer", set(), "designer"),
    ("Security Bot", set(), "security-bot"),
    ("  UX__Writer!! ", set(), "ux-writer"),
    ("設計師", set(), "agent-01"),  # nothing ascii to build from
    ("設計師", {"agent-01", "agent-02"}, "agent-03"),
    ("QA", set(), "agent-01"),  # "qa" is reserved
    ("Designer", {"designer"}, "agent-01"),  # taken
    ("9lives", set(), "agent-01"),  # would start with a digit
    ("X", set(), "agent-01"),  # one letter is too short
    ("abcdefghijklmnopqrstuvwxyz", set(), "abcdefghijklmnopqrstuvwx"),
    ("abcdefghijklmnopqrstuvw yz", set(), "abcdefghijklmnopqrstuvw"),  # no trailing dash after cutting
])
def test_derive_agent_id(display_name, existing, expected):
    derived = derive_agent_id(display_name, existing)
    assert derived == expected
    assert validate_agent_id(derived) == []


# -- lineups -------------------------------------------------------------------------------------

ROSTER = {
    "pm": "make", "swe": "make", "qa": "check",
    "designer": "make", "security": "check", "writer": "make",
}


@pytest.mark.parametrize("seats", [
    ["pm", "swe", "qa"],
    ["pm", "designer", "swe", "qa"],
    ["pm", "swe", "qa", "security"],
    ["writer"],
    ["pm", "writer"],
])
def test_valid_lineups(seats):
    assert validate_lineup(seats, ROSTER) == []


@pytest.mark.parametrize(("seats", "fragment"), [
    ([], "1"),
    (["pm", "swe", "qa", "designer", "security", "writer", "pm2", "x", "y"], "8"),
    (["pm", "swe", "pm"], "重複"),
    (["pm", "ghost"], "ghost"),
    (["qa", "security"], "make"),
    (["qa", "pm"], "第一席"),
])
def test_invalid_lineups(seats, fragment):
    roster = {**ROSTER, "pm2": "make", "x": "make", "y": "make"}
    errors = validate_lineup(seats, roster)
    assert errors
    assert any(fragment in e["message"] for e in errors), errors


def test_eight_seats_is_the_maximum():
    roster = {f"s{i}x": "make" for i in range(MAX_SEATS)}
    assert MAX_SEATS == 8
    assert validate_lineup(list(roster), roster) == []


def test_lineup_validation_accepts_seats_as_roster_values():
    roster = {s.id: s for s in BUILTIN_SEATS}
    assert validate_lineup(["pm", "swe", "qa"], roster) == []
    assert validate_lineup(["qa"], roster) != []


# -- LLM choice (D21) ----------------------------------------------------------------------------


@pytest.mark.parametrize("value", [None, "mock", {"provider": "mock"}, *[
    {"provider": "anthropic", "model": m} for m in ANTHROPIC_MODELS
]])
def test_valid_llm_choices(value):
    assert validate_llm_choice(value) == []


@pytest.mark.parametrize("value", [
    {"provider": "anthropic", "model": "claude-3-opus"},  # not on the whitelist
    {"provider": "anthropic"},  # no model
    {"provider": "openai", "model": "gpt-4o"},
    {"provider": "gemini", "model": "gemini-1.5-pro"},
    {"provider": "Anthropic", "model": "claude-opus-5-5"},  # exact names only
    {"provider": "anthropic", "model": "claude-opus-5-5", "api_key_env": "ANTHROPIC_API_KEY"},
    {"provider": "mock", "api_key_env": "X"},
    {"provider": "anthropic", "model": "claude-opus-5-5", "temperature": 0},  # unknown key
    "anthropic",
    "Mock",
    42,
    ["mock"],
])
def test_invalid_llm_choices(value):
    errors = validate_llm_choice(value)
    assert errors
    assert all(e["field"] == "llm" for e in errors)


def test_api_key_env_is_refused_by_name():
    errors = validate_llm_choice({"provider": "anthropic", "model": "claude-opus-5-5", "api_key_env": "X"})
    assert any("api_key_env" in e["message"] for e in errors)


def test_null_is_refused_where_a_value_is_required():
    # The project default can not be "follow the project default".
    assert validate_llm_choice(None, allow_null=False) != []
    assert validate_llm_choice("mock", allow_null=False) == []


def test_every_whitelisted_model_has_a_price():
    assert ANTHROPIC_MODELS == ("claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5", "claude-fable-5-2")
    for model in ANTHROPIC_MODELS:
        assert ("anthropic", model) in MODEL_PRICES, model


# -- AgentView -----------------------------------------------------------------------------------


def test_agent_view_shape_matches_the_frozen_contract():
    effective = {"provider": "mock", "model": "mock-model", "source": "project", "mock_reason": "configured_mock"}
    view = AgentView.from_seat(designer(), llm=None, effective_llm=effective)
    assert view.to_dict() == {
        "id": "designer", "display_name": "設計師", "duty": "畫介面草圖", "kind": "make",
        "look": "beret", "builtin": False, "llm": None, "effective_llm": effective,
    }
    assert list(view.to_dict()) == ["id", "display_name", "duty", "kind", "look", "builtin", "llm", "effective_llm"]


def test_agent_view_dict_is_a_copy():
    llm = {"provider": "anthropic", "model": "claude-opus-5-5"}
    view = AgentView.from_seat(BUILTIN_SEATS[0], llm=llm, effective_llm={"provider": "anthropic"})
    out = view.to_dict()
    out["llm"]["model"] = "changed"
    assert view.to_dict()["llm"]["model"] == "claude-opus-5-5"

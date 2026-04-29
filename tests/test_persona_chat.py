from evaluation.personas.schema import Persona
from evaluation.simulator.persona_chat import render_persona_system_prompt
from tests.test_persona_schema import _full_persona_dict


def _persona() -> Persona:
    return Persona.model_validate(_full_persona_dict())


def test_render_substitutes_all_placeholders() -> None:
    template = (
        "이름:${name}|국적:${nationality}|목표:${goal}|배경:${background_story}|"
        "톤:${tone}|특이점:${quirks}|성공기준:${success_criteria}"
    )
    out = render_persona_system_prompt(template, _persona())
    assert "${" not in out
    assert "테스터" in out
    assert "베트남" in out
    assert "임금 체불" in out
    assert "조사 가끔 누락" in out
    assert "신고 채널이 안내됨" in out


def test_render_handles_empty_quirks() -> None:
    persona_dict = _full_persona_dict()
    persona_dict["communication_style"]["quirks"] = []
    persona = Persona.model_validate(persona_dict)
    out = render_persona_system_prompt("quirks: ${quirks}", persona)
    assert "(없음)" in out


def test_render_unknown_placeholder_is_left_intact() -> None:
    out = render_persona_system_prompt("ok ${unknown_field}", _persona())
    assert "${unknown_field}" in out

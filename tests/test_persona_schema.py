from typing import Any

import pytest
from pydantic import ValidationError

from evaluation.personas.schema import (
    CommunicationStyle,
    Demographics,
    GeneratedWith,
    Persona,
    Task,
    UsageContext,
)


def _full_persona_dict() -> dict[str, Any]:
    return {
        "id": "p_test",
        "name": "테스터",
        "seed_id": "p_test",
        "demographics": {
            "age_band": "30대 초반",
            "gender": "여성",
            "nationality": "베트남",
            "occupation": "제조업",
            "residence": "경기도 안산",
            "korean_proficiency": "초급-중급",
            "digital_literacy": "스마트폰 능숙",
        },
        "context": {
            "when": "퇴근 후 저녁",
            "where": "기숙사",
            "device": "Android 스마트폰",
            "emotional_state": "불안",
        },
        "task": {
            "goal": "임금 체불을 신고하는 절차를 알고 싶음",
            "success_criteria": [
                "신고 채널이 안내됨",
                "보복 방지 정보가 안내됨",
            ],
        },
        "background_story": (
            "안산의 자동차 부품 공장에서 1년 8개월 일하고 있다. "
            "최근 야근수당이 누락된 것을 알게 됐고, 회사에 직접 묻기 두려워 다른 방법을 찾고 있다. "
            "한국어 법률 용어가 어려워 답답하다."
        ),
        "communication_style": {
            "tone": "공손하지만 답답해하는",
            "info_disclosure": "수동적: 묻지 않으면 자세히 말 안 함",
            "complaint_expression": "되묻기로 우회 표현",
            "typical_phrasing": ["저기 잠깐만요", "이게 맞나요?"],
            "quirks": ["조사 가끔 누락"],
        },
        "generated_with": {
            "prompt_id": "persona_generator",
            "prompt_version": "v1",
            "prompt_sha256": "x" * 64,
            "model_id": "gpt-4o-mini",
            "temperature": 0.0,
        },
    }


def test_persona_validates_with_full_data():
    p = Persona.model_validate(_full_persona_dict())
    assert p.id == "p_test"
    assert p.demographics.nationality == "베트남"
    assert len(p.task.success_criteria) == 2
    assert p.communication_style.quirks == ["조사 가끔 누락"]


def test_task_requires_at_least_one_success_criterion():
    bad = _full_persona_dict()
    bad["task"]["success_criteria"] = []
    with pytest.raises(ValidationError):
        Persona.model_validate(bad)


def test_communication_style_requires_typical_phrasing():
    bad = _full_persona_dict()
    bad["communication_style"]["typical_phrasing"] = []
    with pytest.raises(ValidationError):
        Persona.model_validate(bad)


def test_background_story_min_length():
    bad = _full_persona_dict()
    bad["background_story"] = "너무 짧음"
    with pytest.raises(ValidationError):
        Persona.model_validate(bad)


def test_demographics_field_missing():
    bad = _full_persona_dict()
    del bad["demographics"]["nationality"]
    with pytest.raises(ValidationError):
        Persona.model_validate(bad)


def test_quirks_defaults_to_empty_list():
    data = _full_persona_dict()
    del data["communication_style"]["quirks"]
    p = Persona.model_validate(data)
    assert p.communication_style.quirks == []


def test_subobjects_can_be_constructed_directly():
    demographics = Demographics(
        age_band="20대",
        gender="남성",
        nationality="네팔",
        occupation="건설",
        residence="부산",
        korean_proficiency="초급",
        digital_literacy="스마트폰",
    )
    assert demographics.nationality == "네팔"

    ctx = UsageContext(when="새벽", where="병원", device="iPhone", emotional_state="고통")
    assert ctx.where == "병원"

    task = Task(goal="산재 절차 이해", success_criteria=["A"])
    assert task.success_criteria == ["A"]

    style = CommunicationStyle(
        tone="짜증 섞인",
        info_disclosure="수동적",
        complaint_expression="직설적",
        typical_phrasing=["아파요"],
    )
    assert style.quirks == []

    gw = GeneratedWith(
        prompt_id="x",
        prompt_version="v1",
        prompt_sha256="a" * 64,
        model_id="gpt-4o-mini",
        temperature=0.0,
    )
    assert gw.prompt_version == "v1"

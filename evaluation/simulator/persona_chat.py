from string import Template

from ..personas.schema import Persona


def render_persona_system_prompt(template_body: str, persona: Persona) -> str:
    """Render the persona role-play system prompt by substituting ${var} placeholders
    with persona attributes. safe_substitute() leaves unknown placeholders alone so a
    malformed prompt fails loudly later rather than silently dropping content."""
    quirks = persona.communication_style.quirks
    return Template(template_body).safe_substitute(
        name=persona.name,
        age_band=persona.demographics.age_band,
        gender=persona.demographics.gender,
        nationality=persona.demographics.nationality,
        occupation=persona.demographics.occupation,
        residence=persona.demographics.residence,
        korean_proficiency=persona.demographics.korean_proficiency,
        digital_literacy=persona.demographics.digital_literacy,
        when=persona.context.when,
        where=persona.context.where,
        device=persona.context.device,
        emotional_state=persona.context.emotional_state,
        background_story=persona.background_story,
        goal=persona.task.goal,
        success_criteria="; ".join(persona.task.success_criteria),
        tone=persona.communication_style.tone,
        info_disclosure=persona.communication_style.info_disclosure,
        complaint_expression=persona.communication_style.complaint_expression,
        typical_phrasing="; ".join(persona.communication_style.typical_phrasing),
        quirks="; ".join(quirks) if quirks else "(없음)",
    )

import logging
from typing import Any, Dict, List

from langchain_openai import ChatOpenAI


REWRITE_PROMPT = """당신은 사용자의 최신 질문을 직전 대화 맥락 없이도 독립적으로 이해 가능한 형태로 재작성하는 어시스턴트입니다.

규칙:
- 질문의 원래 의도와 정보 요청 범위를 바꾸지 마세요.
- 대명사("그것","그럼","위에서","그 비자")나 생략된 주어/목적어를 직전 대화에서 추론해 명확히 풀어 쓰세요.
- 이미 독립적으로 이해 가능한 질문이면 그대로 출력하세요.
- 질문에 답하지 말고 재작성만 하세요.
- 최신 질문과 동일한 언어로 출력하세요.
- 따옴표·접두사·설명 없이 재작성된 질문 한 줄만 출력하세요.

[직전 대화]
{history}

[최신 질문]
{query}

[재작성된 질문]"""


class QueryRewriter:
    """대화 맥락이 있을 때 새 질문을 독립 질문으로 재작성."""

    def __init__(self, config: Dict[str, Any]):
        chat_config = config.get("chat_config", {})
        self.model_name = chat_config.get("model", "gpt-4o-mini")
        self.llm = ChatOpenAI(
            model=self.model_name,
            temperature=0,
        )

    def rewrite(self, history: List[Dict[str, str]], query: str) -> str:
        if not history:
            return query

        formatted_history = "\n".join(
            f"{'사용자' if msg['speaker'] == 'human' else 'AI'}: {msg['utterance']}"
            for msg in history
        )
        prompt = REWRITE_PROMPT.format(history=formatted_history, query=query)

        try:
            output = self.llm.invoke(prompt)
            rewritten = (output.content or "").strip()
            return rewritten if rewritten else query
        except Exception as exc:
            logging.error("Query rewrite failed (%s); falling back to original", exc)
            return query

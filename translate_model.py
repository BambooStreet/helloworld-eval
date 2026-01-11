import json
import logging
from typing import Any, Dict, List

from langchain_openai import ChatOpenAI

from prompts import prompts


class TranslateModel:
    """Handles query translation and MongoDB pipeline generation."""

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        translate_config = config.get("translate_config", {})
        default_model = config.get("chat_config", {}).get("model", "gpt-4o-mini")
        self.model_name = translate_config.get("model", default_model)
        self.temperature = translate_config.get("temperature", 0.0)
        self.prompt = prompts.load_prompt("query_translate")

        self.llm = ChatOpenAI(
            model=self.model_name,
            temperature=self.temperature,
        )

    def translate_query(self, query_text: str) -> Dict[str, Any]:
        """Translate incoming text and return mongo pipeline + language."""
        if not query_text or not str(query_text).strip():
            raise ValueError("Query text is required for translation")

        messages = [
            {"role": "system", "content": self.prompt},
            {"role": "user", "content": query_text},
        ]

        response = self.llm.invoke(messages)
        content = (response.content or "").strip()

        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as exc:
            logging.error("Failed to parse translation response: %s", exc)
            raise ValueError("Translation model returned invalid JSON") from exc

        translated_query = parsed.get("translated")
        mongo_query = parsed.get("mongo_query")
        query_lang = parsed.get("query_lang")

        if not translated_query or not query_lang:
            raise ValueError("Translation response missing required fields")

        mongo_query = self._normalize_mongo_query(mongo_query)

        return {
            "translated_query": translated_query,
            "mongo_query": mongo_query,
            "query_lang": query_lang,
        }

    @staticmethod
    def _normalize_mongo_query(raw_query: Any) -> List[Dict[str, Any]]:
        """Ensure the mongo pipeline is a list of documents."""
        if raw_query is None:
            return []

        if isinstance(raw_query, list):
            return raw_query

        if isinstance(raw_query, str):
            try:
                parsed = json.loads(raw_query)
                if isinstance(parsed, list):
                    return parsed
            except json.JSONDecodeError:
                logging.warning(
                    "mongo_query string is not valid JSON; defaulting to empty list."
                )
                return []

        logging.warning(
            "mongo_query has unexpected type %s; defaulting to empty list.",
            type(raw_query),
        )
        return []

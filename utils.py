import ast, json

def parse_mongo_query(query_raw):
    # 이미 리스트[dict]
    if isinstance(query_raw, list):
        return query_raw
    # 문자열이면 ast 우선 → json → 마지막으로 단순치환 후 json
    if isinstance(query_raw, str):
        for parser in (ast.literal_eval, json.loads):
            try:
                return parser(query_raw)
            except Exception:
                pass
        # 단순 따옴표 치환 시도 (가능한 경우에만)
        try:
            sanitized = query_raw.replace("'", '"')
            return json.loads(sanitized)
        except Exception as e:
            raise e
    raise ValueError("Unsupported mongo_query type: {}".format(type(query_raw)))

import logging
import os
import sys
from datetime import datetime


_LOGGING_CONFIGURED = False


class _StreamToLogger:
    def __init__(self, logger: logging.Logger, level: int) -> None:
        self.logger = logger
        self.level = level
        self._buffer = ""

    def write(self, message: str) -> None:
        if message and message != "\n":
            self._buffer += message
            while "\n" in self._buffer:
                line, self._buffer = self._buffer.split("\n", 1)
                if line.strip():
                    self.logger.log(self.level, line)

    def flush(self) -> None:
        if self._buffer.strip():
            self.logger.log(self.level, self._buffer.strip())
            self._buffer = ""

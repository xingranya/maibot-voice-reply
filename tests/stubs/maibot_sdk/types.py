"""maibot_sdk.types 桩模块。"""

from __future__ import annotations

from enum import Enum
from typing import Any


class ActivationType(str, Enum):
    NEVER = "never"
    ALWAYS = "always"
    RANDOM = "random"
    KEYWORD = "keyword"


class ErrorPolicy(str, Enum):
    ABORT = "abort"
    SKIP = "skip"
    LOG = "log"


class HookMode(str, Enum):
    BLOCKING = "blocking"
    NON_BLOCKING = "non_blocking"


class HookOrder(str, Enum):
    EARLY = "early"
    NORMAL = "normal"
    LATE = "late"


class ToolParamType(str, Enum):
    STRING = "string"
    INTEGER = "integer"
    NUMBER = "number"
    FLOAT = "float"
    BOOLEAN = "boolean"
    ARRAY = "array"
    OBJECT = "object"


class ToolParameterInfo:
    def __init__(self, **kwargs: Any) -> None:
        self.data = kwargs


def _make(name: str):
    from . import _Decorator

    def factory(*args: Any, **kwargs: Any) -> _Decorator:
        return _Decorator(kind=name, args=args, **kwargs)

    return factory


Tool = _make("Tool")

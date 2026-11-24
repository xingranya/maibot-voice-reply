"""maibot_sdk 桩模块。

只为让 ``core/`` 与 ``plugin.py`` 能在普通 Python 环境里导入与单测，
不模拟 RPC 行为。真实运行时由 MaiBot Runner 注入真正的 SDK。

配置基类直接基于 pydantic，与真实 SDK 行为一致（嵌套段、校验、model_dump）。
"""

from __future__ import annotations

from typing import Any, Callable

from pydantic import BaseModel, ConfigDict, Field as _PydanticField


_UNSET = object()


def Field(default: Any = _UNSET, **kwargs: Any) -> Any:  # noqa: N802
    """转发到 pydantic.Field。

    真实 SDK 的 ``Field`` 也是 pydantic 的再导出，签名一致；这里保留
    ``default_factory`` 透传，未传 default 时不占位。
    """
    if default is _UNSET:
        return _PydanticField(**kwargs)
    return _PydanticField(default, **kwargs)


class PluginConfigBase(BaseModel):
    """配置基类桩，行为对齐真实 SDK。"""

    model_config = ConfigDict(extra="allow", validate_assignment=True)

    @classmethod
    def model_validate(cls, obj: Any, **kwargs: Any) -> "PluginConfigBase":
        return super().model_validate(obj, **kwargs)


class _Decorator:
    """占位装饰器：直接返回原函数并附着元数据。"""

    def __init__(self, **meta: Any) -> None:
        self.meta = meta

    def __call__(self, func: Callable[..., Any]) -> Callable[..., Any]:
        setattr(func, "__stub_meta__", self.meta)
        return func


def _make(name: str) -> Callable[..., Any]:
    def factory(*args: Any, **kwargs: Any) -> _Decorator:
        return _Decorator(kind=name, args=args, **kwargs)

    return factory


API = _make("API")
Command = _make("Command")
HomeCard = _make("HomeCard")
HookHandler = _make("HookHandler")
Tool = _make("Tool")


class ConfigWidget:
    """控件声明桩。"""

    def __init__(self, **kwargs: Any) -> None:
        self.data = kwargs


class ConfigWidgetState:
    """控件状态桩。"""

    def __init__(self, **kwargs: Any) -> None:
        self.data = kwargs


class MaiBotPlugin:
    """插件基类桩。"""

    config_model: Any = None
    config_widgets: list[Any] = []

    def __init__(self) -> None:
        self._plugin_config_instance: Any = None
        self._plugin_config_data: dict[str, Any] = {}
        self._ctx: Any = None

    @property
    def ctx(self) -> Any:
        return self._ctx

    @property
    def config(self) -> Any:
        if self._plugin_config_instance is None:
            raise RuntimeError("当前插件配置尚未完成初始化")
        return self._plugin_config_instance

    @classmethod
    def get_config_model(cls) -> Any:
        return cls.config_model

"""测试环境准备。

插件用相对导入（``from .core import ...``），MaiBot 的 loader 通过
``submodule_search_locations`` 把它当成包加载。测试里没有 loader，所以这里
手工做同样的事：以 ``maibot_voice_reply`` 为包名加载，再把模块暴露成
``plugin``，测试代码就能照常 ``import plugin``。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
STUBS = HERE / "stubs"
ROOT = HERE.parent

for path in (str(STUBS), str(ROOT)):
    if path not in sys.path:
        sys.path.insert(0, path)

PKG_NAME = "maibot_voice_reply"

if PKG_NAME not in sys.modules:
    spec = importlib.util.spec_from_file_location(
        PKG_NAME,
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("无法为插件创建包 spec")
    package = importlib.util.module_from_spec(spec)
    sys.modules[PKG_NAME] = package
    # 插件目录没有 __init__.py，用一个空包顶替
    package.__path__ = [str(ROOT)]  # type: ignore[attr-defined]

plugin_spec = importlib.util.spec_from_file_location(
    f"{PKG_NAME}.plugin",
    ROOT / "plugin.py",
)
if plugin_spec is None or plugin_spec.loader is None:
    raise RuntimeError("无法加载 plugin.py")
plugin_module = importlib.util.module_from_spec(plugin_spec)
sys.modules[f"{PKG_NAME}.plugin"] = plugin_module
plugin_spec.loader.exec_module(plugin_module)
# 让 `import plugin` 拿到同一个模块对象
sys.modules["plugin"] = plugin_module

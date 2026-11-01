"""Langflow 컴포넌트 `Perdix Agent`의 모델 선택 규칙.

컴포넌트는 Langflow 런타임(lfx, langchain_core)을 import한다.
여기서는 그 모듈을 얇은 가짜로 바꿔 끼워 파일을 그대로 불러온다.
확인하는 것은 컴포넌트 자신의 규칙뿐이다.
- Model 칸이 비면 역할 할당(`perdix:role/<역할>`), 값이 있으면 그 모델(`perdix:<모델>`)
- Model 칸 새로고침은 중계 `/v1/models` 목록으로 채우고, 실패하면 비운다
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path
from typing import Any

import httpx
import pytest

COMPONENT = (Path(__file__).resolve().parents[2]
             / "integrations/langflow/components/perdix/perdix_agent.py")


def _stub_modules(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Field:
        def __init__(self, **kwargs: Any) -> None:
            self.__dict__.update(kwargs)

    class _Component:
        pass

    lfx_io = types.ModuleType("lfx.io")
    for name in ("DropdownInput", "Output", "SecretStrInput", "StrInput"):
        setattr(lfx_io, name, _Field)
    component_mod = types.ModuleType("lfx.custom.custom_component.component")
    component_mod.Component = _Component  # type: ignore[attr-defined]
    message_mod = types.ModuleType("lfx.schema.message")
    message_mod.Message = _Field  # type: ignore[attr-defined]
    tools_mod = types.ModuleType("langchain_core.tools")
    tools_mod.StructuredTool = _Field  # type: ignore[attr-defined]
    tools_mod.ToolException = Exception  # type: ignore[attr-defined]
    for name, module in {
        "lfx": types.ModuleType("lfx"), "lfx.io": lfx_io,
        "lfx.custom": types.ModuleType("lfx.custom"),
        "lfx.custom.custom_component": types.ModuleType("lfx.custom.custom_component"),
        "lfx.custom.custom_component.component": component_mod,
        "lfx.schema": types.ModuleType("lfx.schema"), "lfx.schema.message": message_mod,
        "langchain_core": types.ModuleType("langchain_core"), "langchain_core.tools": tools_mod,
    }.items():
        monkeypatch.setitem(sys.modules, name, module)


@pytest.fixture
def component_module(monkeypatch: pytest.MonkeyPatch) -> types.ModuleType:
    _stub_modules(monkeypatch)
    spec = importlib.util.spec_from_file_location("perdix_agent_under_test", COMPONENT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_empty_model_follows_role_assignment(component_module: types.ModuleType) -> None:
    assert component_module.model_target("build", "") == "perdix:role/build"
    assert component_module.model_target("build", None) == "perdix:role/build"
    assert component_module.model_target("build", "   ") == "perdix:role/build"


def test_chosen_model_is_called_directly(component_module: types.ModuleType) -> None:
    chosen = component_module.model_target("build", " openai/gpt-6-luna ")
    assert chosen == "perdix:openai/gpt-6-luna"


def test_model_field_exists_and_defaults_to_empty(component_module: types.ModuleType) -> None:
    fields = {f.name: f for f in component_module.PerdixAgentComponent.inputs}
    assert fields["model_override"].value == ""
    assert fields["model_override"].refresh_button is True


def _component(module: types.ModuleType) -> Any:
    component = module.PerdixAgentComponent()
    component.relay_url = "http://127.0.0.1:18797/"
    component.relay_token = "tok"
    return component


def test_refresh_fills_options_from_relay(component_module: types.ModuleType,
                                          monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    def fake_get(url: str, headers: dict[str, str], timeout: float) -> httpx.Response:
        seen.update(url=url, headers=headers)
        return httpx.Response(200, json={"data": [{"id": "a/one"}, {"id": "b/two"}]},
                              request=httpx.Request("GET", url))

    monkeypatch.setattr(component_module.httpx, "get", fake_get)
    config = {"model_override": {"options": []}}
    _component(component_module).update_build_config(config, "", "model_override")
    assert config["model_override"]["options"] == ["a/one", "b/two"]
    assert seen == {"url": "http://127.0.0.1:18797/v1/models",
                    "headers": {"authorization": "Bearer tok"}}


def test_refresh_clears_options_when_relay_is_down(component_module: types.ModuleType,
                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_get(url: str, headers: dict[str, str], timeout: float) -> httpx.Response:
        raise httpx.ConnectError("refused", request=httpx.Request("GET", url))

    monkeypatch.setattr(component_module.httpx, "get", fake_get)
    config = {"model_override": {"options": ["stale"]}}
    _component(component_module).update_build_config(config, "", "model_override")
    assert config["model_override"]["options"] == []

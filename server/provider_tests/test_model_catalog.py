from pathlib import Path

import pytest

from perdix.acp.agent import PerdixAgent
from perdix.config.schema import ServerConfig
from perdix.llm import catalog
from perdix.llm.ollama import OllamaProvider
from perdix.memory.schema import MemoryConfig

POPULAR = (
    "deepseek/deepseek-v4.1-flash", "moonshotai/kimi-k3", "z-ai/glm-5.3",
    "qwen/qwen3.8-flash", "minimax/minimax-m3", "google/gemini-4-argon",
    "claude-fable-5-1", "claude-haiku-5-5", "gemini-4-argon",
)

OLLAMA_MODELS = ("qwen3:4b", "gemma3:4b", "gemma3:12b")


def test_ollama_provider_uses_local_endpoint_without_auth_profile(tmp_path: Path) -> None:
    config = ServerConfig(
        provider="ollama",
        model="qwen3:4b",
        ollama_base_url="http://localhost:11434/v1",
        db_path=str(tmp_path / "sessions.db"),
        credentials_path=tmp_path / "credentials.json",
        memories=MemoryConfig(enabled=False),
    )
    agent = PerdixAgent(config)

    selected = agent._provider_for("qwen3:4b")

    assert isinstance(selected, OllamaProvider)
    assert selected.name == "ollama"
    assert selected.base_url == "http://localhost:11434/v1/"
    assert catalog.get("qwen3:4b").provider == "ollama"


@pytest.mark.asyncio
async def test_ollama_models_show_deferred_pull_prompt(tmp_path: Path) -> None:
    agent = PerdixAgent(ServerConfig(
        provider="ollama", model="qwen3:4b", db_path=str(tmp_path / "sessions.db"),
        credentials_path=tmp_path / "credentials.json", memories=MemoryConfig(enabled=False),
    ))
    try:
        session = await agent.new_session(str(tmp_path))
        model = next(option for option in session.config_options or [] if option.id == "model")
        options = {option.value: option for option in model.options}

        for model_id in OLLAMA_MODELS:
            assert options[model_id].description == (
                f"provider: ollama\n미설치 시 실행: ollama pull {model_id}"
            )
    finally:
        await agent.aclose()


@pytest.mark.asyncio
async def test_models_are_advertised_selectable_and_restored_without_refresh(
    tmp_path: Path,
) -> None:
    # Given: 네트워크 갱신 없이 생성한 일반 ACP 세션이 있다.
    agents = tmp_path / "agents.json"
    agents.write_text("{}")
    config = ServerConfig(db_path=str(tmp_path / "sessions.db"), agents_config_path=agents,
                          credentials_path=tmp_path / "credentials.json",
                          memories=MemoryConfig(enabled=False))
    agent = PerdixAgent(config)
    try:
        session = await agent.new_session(str(tmp_path))
        model = next(option for option in session.config_options or [] if option.id == "model")
        assert set(POPULAR) <= {option.value for option in model.options}
        # When: 새 모델을 표준 설정 메서드로 선택한다.
        for model_id in POPULAR:
            response = await agent.set_config_option("model", session.session_id, model_id)
            selected = next(option for option in response.config_options if option.id == "model")
            assert selected.current_value == model_id
            assert agent._provider_name_for(model_id) == catalog.get(model_id).provider
        session_id = session.session_id
    finally:
        await agent.aclose()
    # Then: 서버 재생성 이후에도 마지막 모델 선택이 유지된다.
    restarted = PerdixAgent(config)
    try:
        loaded = await restarted.load_session(str(tmp_path), session_id)
        assert loaded is not None
        selected = next(option for option in loaded.config_options or [] if option.id == "model")
        assert selected.current_value == POPULAR[-1]
    finally:
        await restarted.aclose()


@pytest.mark.asyncio
async def test_popular_model_can_be_assigned_without_remote_refresh(tmp_path: Path) -> None:
    # Given: 원격 모델 목록을 불러오지 않은 에이전트가 있다.
    agents = tmp_path / "agents.json"
    agents.write_text("{}")
    agent = PerdixAgent(ServerConfig(db_path=str(tmp_path / "sessions.db"),
        agents_config_path=agents, credentials_path=tmp_path / "credentials.json",
        memories=MemoryConfig(enabled=False)))
    try:
        session = await agent.new_session(str(tmp_path))
        # When: 역할 모델을 기본 스냅샷에 포함된 DeepSeek으로 할당한다.
        assigned = await agent.set_config_option(
            "agent_model_explore", session.session_id, POPULAR[0],
        )
        # Then: 갱신 요청 없이 할당이 저장되고 표준 설정 응답에 반영된다.
        selected = next(option for option in assigned.config_options
                        if option.id == "agent_model_explore")
        assert selected.current_value == POPULAR[0]
        assert agents.exists()
    finally:
        await agent.aclose()

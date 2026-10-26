from __future__ import annotations

from acp import RequestError
from acp.schema import SessionConfigOptionSelect, SessionConfigSelectOption

from perdix.config.auth_profiles import LEGACY, PROVIDERS, AuthProfileError, AuthProfiles
from perdix.session.store import Event, EventStore

AUTH_PREFIX = "auth_profile_"
AUTH_HELP = ("/auth status | /auth list | "
             "/auth use <anthropic|openai|gemini|openrouter> <프로필|legacy>")


class SessionAuthProfiles:
    def __init__(self, profiles: AuthProfiles) -> None:
        self.profiles = profiles
        self.selections: dict[str, dict[str, str]] = {}

    async def create(self, store: EventStore, session_id: str) -> None:
        selected = self.profiles.defaults()
        for provider, name in selected.items():
            await store.append(session_id, "config_change",
                               {"key": AUTH_PREFIX + provider, "value": name})
        self.selections[session_id] = selected

    def restore(self, session_id: str, events: list[Event]) -> None:
        selected: dict[str, str] = {provider: LEGACY for provider in PROVIDERS}
        for event in events:
            if event.kind == "config_change":
                change = event.as_config_change()
                provider = change.key.removeprefix(AUTH_PREFIX)
                if change.key.startswith(AUTH_PREFIX) and provider in selected:
                    if isinstance(change.value, str):
                        selected[provider] = change.value
        self.selections[session_id] = selected

    def selection(self, session_id: str, provider: str) -> str:
        return self.selections.get(session_id, {}).get(provider, LEGACY)

    def options(self, session_id: str) -> list[SessionConfigOptionSelect]:
        registry = self.profiles.read()
        result: list[SessionConfigOptionSelect] = []
        for provider in PROVIDERS:
            current = self.selection(session_id, provider)
            choices = [SessionConfigSelectOption(value=LEGACY, name="legacy · 기존 인증")]
            for profile in registry.profiles.values():
                if profile.provider == provider:
                    choices.append(SessionConfigSelectOption(
                        value=profile.name, name=self.profiles.describe(provider, profile.name),
                    ))
            if current not in {choice.value for choice in choices}:
                choices.append(SessionConfigSelectOption(value=current, name=current + " · 없음"))
            result.append(SessionConfigOptionSelect(
                id=AUTH_PREFIX + provider, name=f"인증 · {provider}", type="select",
                current_value=current, options=choices,
            ))
        return result

    async def set_option(self, store: EventStore, session_id: str,
                         config_id: str, value: str | bool) -> None:
        provider = config_id.removeprefix(AUTH_PREFIX)
        if provider not in PROVIDERS or not isinstance(value, str):
            raise RequestError.invalid_params({"reason": "인증 제공자와 프로필을 확인하세요"})
        try:
            self.profiles.read().get_profile(provider, value)
        except AuthProfileError as exc:
            raise RequestError.invalid_params({"reason": str(exc)}) from exc
        await store.append(session_id, "config_change", {"key": config_id, "value": value})
        self.selections[session_id][provider] = value

    def status(self, session_id: str) -> str:
        return "\n".join(self.profiles.describe(provider, self.selection(session_id, provider))
                         for provider in PROVIDERS)

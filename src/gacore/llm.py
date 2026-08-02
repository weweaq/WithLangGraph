"""LLM factory: build a provider-specific chat model bound to the given tools.

Replaces GenericAgent's NativeToolClient/mykey.py path with standard LangChain
constructors driven by environment variables (see .env.example).
"""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from typing import Final

from langchain_anthropic import ChatAnthropic
from langchain_core.language_models import LanguageModelInput
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.runnables import Runnable
from langchain_core.tools import BaseTool
from langchain_openai import ChatOpenAI

from gacore.config import ConfigError

_SUPPORTED_PROVIDERS: Final = ("openai", "anthropic")
_DEFAULT_OPENAI_MODEL: Final = "gpt-4o"
_DEFAULT_ANTHROPIC_MODEL: Final = "claude-sonnet-4-5"


class MissingApiKeyError(ValueError):
    """Raised when the active provider's API key is absent from the environment."""


def get_provider(env: Mapping[str, str] | None = None) -> str:
    """Return the lowercased LLM provider name from the environment.

    Raises ConfigError when LLM_PROVIDER is unset or not a supported provider.
    """
    source = os.environ if env is None else env
    provider = (source.get("LLM_PROVIDER") or "").strip().lower()
    if provider not in _SUPPORTED_PROVIDERS:
        raise ConfigError(f"LLM_PROVIDER must be one of {_SUPPORTED_PROVIDERS}, got {provider!r}")
    return provider


def get_llm(
    tool_list: Sequence[BaseTool],
    env: Mapping[str, str] | None = None,
) -> Runnable[LanguageModelInput, AIMessage]:
    """Build a chat model for the env-named provider, bound to the given tools.

    Raises MissingApiKeyError (a ValueError) when the provider's API key is missing.
    """
    source = os.environ if env is None else env
    provider = get_provider(source)
    match provider:  # noqa: MATCH_OK - provider is a validated str, not a closed union
        case "openai":
            api_key = source.get("OPENAI_API_KEY")
            if not api_key:
                raise MissingApiKeyError("OPENAI_API_KEY is required when LLM_PROVIDER=openai")
            llm: BaseChatModel = ChatOpenAI(
                model=source.get("OPENAI_MODEL") or _DEFAULT_OPENAI_MODEL,
                api_key=api_key,
                base_url=source.get("OPENAI_BASE_URL") or None,
                temperature=0,
            )
        case "anthropic":
            api_key = source.get("ANTHROPIC_API_KEY")
            if not api_key:
                raise MissingApiKeyError("ANTHROPIC_API_KEY is required when LLM_PROVIDER=anthropic")
            llm = ChatAnthropic(
                model=source.get("ANTHROPIC_MODEL") or _DEFAULT_ANTHROPIC_MODEL,
                api_key=api_key,
            )
        case _:
            raise ConfigError(f"LLM_PROVIDER must be one of {_SUPPORTED_PROVIDERS}, got {provider!r}")
    return llm.bind_tools(list(tool_list))

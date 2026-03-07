"""Shared model factory: Gemini 2.0 Flash (default) → DeepSeek (fallback)."""
import os

from pydantic_ai.models.openai import OpenAIModel
from pydantic_ai.providers.openai import OpenAIProvider

try:
    from pydantic_ai.models.fallback import FallbackModel
    _has_fallback = True
except ImportError:
    _has_fallback = False


def make_model():
    gemini = OpenAIModel(
        'gemini-2.0-flash-lite',
        provider=OpenAIProvider(
            base_url='https://generativelanguage.googleapis.com/v1beta/openai/',
            api_key=os.environ.get('GEMINI_API_KEY', ''),
        ),
    )
    deepseek = OpenAIModel(
        'deepseek-chat',
        provider=OpenAIProvider(
            base_url='https://api.deepseek.com',
            api_key=os.environ.get('DEEPSEEK_API_KEY', ''),
        ),
    )
    if _has_fallback and os.environ.get('GEMINI_API_KEY'):
        return FallbackModel(gemini, deepseek)
    return deepseek

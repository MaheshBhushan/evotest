from .base import LanguageAdapter
from .registry import detect_language, detect_languages, get_adapter, get_adapter_for_language, resolve_language, resolve_languages

__all__ = [
    "LanguageAdapter",
    "detect_language",
    "detect_languages",
    "get_adapter",
    "get_adapter_for_language",
    "resolve_language",
    "resolve_languages",
]

"""
Language extraction configuration registry.

Maps Language enum values to their corresponding LanguageConfig instances.
"""

from ...types import Language as LangEnum
from .base import LanguageConfig

# Import all language configs
from .python_cfg import PythonConfig
from .javascript_cfg import JavaScriptConfig
from .typescript_cfg import TypeScriptConfig
from .go_cfg import GoConfig
from .java_cfg import JavaConfig
from .rust_cfg import RustConfig

# Registry: maps Language enum -> LanguageConfig instance
_REGISTRY: dict[LangEnum, LanguageConfig] = {}

def _register(config_cls: type[LanguageConfig], *langs: LangEnum):
    """Register a config class for one or more language enums."""
    instance = config_cls()
    for lang in langs:
        _REGISTRY[lang] = instance

# Register all supported languages
_register(PythonConfig, LangEnum.PYTHON)
_register(JavaScriptConfig, LangEnum.JAVASCRIPT, LangEnum.JSX)
_register(TypeScriptConfig, LangEnum.TYPESCRIPT, LangEnum.TSX)
_register(GoConfig, LangEnum.GO)
_register(JavaConfig, LangEnum.JAVA)
_register(RustConfig, LangEnum.RUST)


def get_config(lang: LangEnum) -> LanguageConfig | None:
    """Get the LanguageConfig for a given Language enum value."""
    return _REGISTRY.get(lang)


def has_config(lang: LangEnum) -> bool:
    """Check if a language has a tree-sitter extraction config."""
    return lang in _REGISTRY


def supported_languages() -> list[LangEnum]:
    """Return list of languages with tree-sitter extraction support."""
    return list(_REGISTRY.keys())

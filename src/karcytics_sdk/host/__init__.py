"""Host Tier — Core-facing subsystems and utilities for the Karcytics application and SDK CLI."""

from .core_services import CoreServicesClient, CoreServicesServer, RemoteCapabilityProxy
from .docs import PluginDocumentation, docs_registry
from .sign_plugin import sign_plugin
from .trust_manager import KARCYTICS_ROOT_PUBLIC_KEY_HEX, TrustManager, VerificationResult
from .trust_overrides import LocalTrustRegistry
from .trust_path import TrustChain
from .trust_storage import TrustCache

_AI_SYMBOLS = {"AIAssistant", "AIServerManager", "ai_manager"}

__all__ = [
    "CoreServicesClient",
    "CoreServicesServer",
    "RemoteCapabilityProxy",
    "QtThreadBridge",
    "TrustManager",
    "VerificationResult",
    "LocalTrustRegistry",
    "TrustChain",
    "TrustCache",
    "AIAssistant",
    "AIServerManager",
    "ai_manager",
    "PluginDocumentation",
    "docs_registry",
    "sign_plugin",
    "KARCYTICS_ROOT_PUBLIC_KEY_HEX",
]


def __getattr__(name: str):
    """Lazy loader for symbols with heavy dependencies.

    AI symbols need `requests`; `QtThreadBridge` needs PySide6 widgets, which
    on a headless Linux runner fail without system GL libraries — keeping it
    lazy lets the SDK CLI (signing, license checks) run there.
    """
    if name == "QtThreadBridge":
        from .qt_bridge import QtThreadBridge  # noqa: PLC0415

        globals()["QtThreadBridge"] = QtThreadBridge
        return QtThreadBridge
    if name in _AI_SYMBOLS:
        from .ai import AIAssistant, AIServerManager, ai_manager  # noqa: PLC0415

        globals()["AIAssistant"] = AIAssistant
        globals()["AIServerManager"] = AIServerManager
        globals()["ai_manager"] = ai_manager
        return globals()[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

# 🧬 Karcytics SDK: Developer Portal

Welcome to the official developer portal and documentation site for the **Karcytics Software Development Kit (SDK)**!

Karcytics SDK is a state-of-the-art, high-performance Python framework designed to build secure, multi-threaded, and theme-compliant scientific analysis plugins. It is engineered with robust cryptographic trust controls, decoupled UI/engine execution boundaries, and seamless thread pool delegation.

---

## 🏛️ Codebase Map: What is Where?

A developer looking at the repository files should be able to navigate instantly. Here is a comprehensive breakdown of the file system and module layout:

```text
karcytics-sdk/
├── src/                          # Main source files
│   └── karcytics_sdk/
│       ├── plugin/               # Foundation for plugin developers
│       │   ├── base.py           # Core PluginBase and PluginState definitions
│       │   ├── components.py     # Custom semantic UI buttons and cards
│       │   ├── dialogs.py        # Themed dialog prompts and input wrappers
│       │   ├── wizard.py         # Multi-step guided wizard framework
│       │   ├── analysis.py       # Non-blocking AnalysisBase background thread interfaces
│       │   ├── logging.py        # Injected PluginLoggerAdapter console loggers
│       │   ├── io.py             # Persistent JSON configurations (PluginConfig)
│       │   └── preferences.py    # PreferenceManagerProtocol abstractions
│       │
│       ├── host/                 # Host Application Integrations & Security
│       │   ├── trust_manager.py  # Standard-based signature verification engine
│       │   ├── trust_overrides.py# Silently signed local user trust registries
│       │   ├── trust_path.py     # Structured manifest path serializations
│       │   ├── docs.py           # Programmatic API documentation indices
│       │   └── ai.py             # AI Assistants and inference endpoints
│       │
│       └── sdk_cli.py            # Argparse Unified CLI (Identity manager, signing, shims)
│
├── examples/                     # Production-grade Sandbox Blueprints
│   ├── hello_world/              # Minimal panel, semantic components, and status alerts
│   ├── background_task/          # Multi-threaded calculations using QThreadPool and progress bars
│   └── themed_wizard/            # Dynamic guided setup forms, local configs, and theme adapters
│
├── tests/                        # Comprehensive Headless testing suite
│   ├── conftest.py               # Shared QApplication event loops and fixtures
│   ├── test_plugin_state.py      # Assertions for state captures and undo history
│   ├── test_ui_components.py     # Graphic interactions and semantic layouts
│   └── ...
│
└── docs/                         # Structured developer handbook files
```

---

## 🗺️ Visual Documentation Roadmap

To get started quickly, follow these curated pathways:

```mermaid
graph TD
    A["Welcome Portal"] -->|Start Here| Z["0. Onboarding Guide"]
    Z -->|Quick Setup| B["1. Getting Started"]
    A -->|Build Interfaces| C["2. Developer Handbook"]
    A -->|Signing & Deployment| D["3. Cryptography & CI/CD"]
    A -->|API Blueprints| E["4. API Reference Overview"]
    A -->|Architecture| F["5. Architecture Deep Dive"]

    style A fill:#4CC9F0,stroke:#4CC9F0,stroke-width:2px,color:#fff
    style Z fill:#4361EE,stroke:#4361EE,stroke-width:2px,color:#fff
    style B fill:#4895EF,stroke:#4895EF,stroke-width:2px,color:#fff
    style C fill:#4895EF,stroke:#4895EF,stroke-width:2px,color:#fff
    style D fill:#560BAD,stroke:#560BAD,stroke-width:2px,color:#fff
    style E fill:#7209B7,stroke:#7209B7,stroke-width:2px,color:#fff
    style F fill:#F72585,stroke:#F72585,stroke-width:2px,color:#fff
```

### 🧭 Navigating the Pillars:

0.  **🚀 [Developer Onboarding](Developer_Onboarding.md):** The ultimate master guide for new and existing V1 developers migrating to the Karcytics V2 architecture. Start here!
1.  **🏁 [Quickstart Guide](Getting_Started.md):** Learn how to prepare your local virtual environment using `uv`, generate onboarding keys, bootstrap a new plugin repository, and cryptographically sign your work.
2.  **📖 [Developer Handbook](Dev_Handbook.md):** Deep technical workflows on layouts, dynamic state binding, persistent local storage, and the PySide6 event model.
3.  **🛡️ [CI/CD & Security](CI_CD_Guide.md):** Learn how the Karcytics security model signs code, performs integrity checks, and integrates into GitHub Actions workflows.
4.  **🔍 API Reference:** Comprehensive technical references generated directly from the source code:
    *   **[Plugin Core](api/plugin/base.md):** `PluginBase`, `PluginState`, UI Components, and more.
    *   **[Host & Trust](api/host/trust.md):** `TrustManager`, Plugin Signing, and AI integration.
    *   **[Contrib Utils](api/contrib/image_utils.md):** Helpful pure functions for plugin tasks.
    *   **[Interfaces](api/interfaces.md):** Protocols outlining the boundaries of the SDK.
5.  **🧠 [Architectural Design](Architectural_Design.md):** Deep dive into the architectural principles (RAII cleanup, DIP preferences, headless QTest event orchestration).

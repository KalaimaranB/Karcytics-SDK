# 🚀 Ultimate Developer Onboarding Guide

Welcome to the **Karcytics SDK Developer Ecosystem**! Whether you are a computational biologist, software engineer, or a lab automation specialist, this guide is designed to take you from a fresh installation to publishing your first cryptographically signed Karcytics plugin.

This master guide covers **everything** you need to know, combining elements from our core documentation into one seamless onboarding journey.

---

## 🗺️ The Onboarding Journey

1. **[Phase 1: Environment Setup](#phase-1-environment-setup)**
2. **[Phase 2: Cryptographic Identity](#phase-2-cryptographic-identity)**
3. **[Phase 3: Bootstrapping Your First Plugin](#phase-3-bootstrapping-your-first-plugin)**
4. **[Phase 4: Developing the User Interface](#phase-4-developing-the-user-interface)**
5. **[Phase 5: Code Signing & Security](#phase-5-code-signing-security)**
6. **[Phase 6: CI/CD & Publishing](#phase-6-cicd-publishing)**

---

## 🛠️ Phase 1: Environment Setup

Karcytics relies heavily on modern Python standards and ultra-fast dependency management using **`uv`**.

### 1. Create a Virtual Environment
You should always isolate your development environment to prevent version conflicts.
```bash
# Create the virtual environment using uv
uv venv

# Activate on macOS/Linux
source .venv/bin/activate

# Activate on Windows Powershell
.venv\Scripts\Activate.ps1
```

### 2. Install the Karcytics SDK
Install the SDK into your activated virtual environment. This will expose the `karcytics-sdk` CLI globally within your environment.
```bash
uv pip install -e .
```

*For more details on the environment setup, read the [Quickstart Guide](Getting_Started.md).*

---

## 🔑 Phase 2: Cryptographic Identity

Before you can write code, you must establish a developer identity. The Karcytics host application requires all loaded plugins to be signed by a trusted developer to prevent malicious code execution.

Run the identity initialization command:
```bash
karcytics-sdk init-identity
```

**What this does:**
- Generates an asymmetric **Ed25519** private key (`~/.karcytics/dev_keys/private.key`). **NEVER SHARE THIS FILE.**
- Generates a public key certificate (`~/.karcytics/dev_keys/public.pub`).
- Registers you as an authenticated developer on your local machine.

*For a deep dive into our Trust Engine, read the [Security & CI/CD Guide](CI_CD_Guide.md).*

---

## 🏗️ Phase 3: Bootstrapping Your First Plugin

Instead of manually creating boilerplate files, the Karcytics SDK provides a scaffolding tool.

```bash
karcytics-sdk bootstrap my_first_plugin
```

This generates a robust plugin structure:
```text
my_first_plugin/
├── pyproject.toml           # The plugin manifest (metadata & configuration)
├── src/
│   ├── __init__.py          # The entry point
│   └── ui.py                # The User Interface (PySide6)
└── .github/workflows/       # Pre-configured CI/CD pipelines
```

### Understanding the Manifest (`pyproject.toml`)
The `pyproject.toml` file contains standard Python packaging data alongside Karcytics-specific metadata:
```toml
[tool.karcytics.plugin]
id = "my_first_plugin"
min_core_version = "1.4.9"
entry_point = "my_first_plugin:get_panel_class"
```

---

## 🖥️ Phase 4: Developing the User Interface

Karcytics plugins are natively built using **PySide6**. To ensure a cohesive visual aesthetic across the entire platform, the SDK provides pre-built, styled UI components.

Open `src/ui.py` and import the Karcytics SDK components:

```python
from karcytics_sdk.plugin import PluginBase, PrimaryButton, SecondaryButton, SectionHeader


class MyPluginUI(PluginBase):
    def __init__(self, parent=None):
        super().__init__(parent)

        # Add a stylized header
        header = SectionHeader("Gel Analysis Pipeline", self)
        self.layout.addWidget(header)

        # Add a primary call-to-action button
        btn = PrimaryButton("Run Analysis", self)
        btn.clicked.connect(self._run_analysis)
        self.layout.addWidget(btn)

    def _run_analysis(self):
        print("Analysis started...")
```

*For advanced UI state binding, threading, and local storage, read the [Developer Handbook](Dev_Handbook.md).*

---

## 🔐 Phase 5: Code Signing & Security

Once your code is ready, you must sign it before distributing it to the community or running it in a production Karcytics instance.

Run the signing tool from the root of your plugin directory:
```bash
karcytics-sdk sign my_first_plugin
```

**What this does:**
1. Computes a **SHA-256 integrity hash** for every file in your `src/` folder.
2. Generates a `security.json` ledger.
3. Cryptographically signs the ledger using your private key, outputting `signature.bin`.
4. Attaches a `trust_chain.json` file to establish your identity.

---

## 🚀 Phase 6: CI/CD & Publishing

When you bootstrap a plugin, the SDK automatically generates GitHub Actions workflows in `.github/workflows/`. These are designed to automate your supply chain:

1. **`ci.yml`**: Runs your tests and linters on every pull request.
2. **`deploy-docs.yml`**: Automatically generates and deploys MkDocs documentation to GitHub Pages.
3. **`release.yml`**: Automatically packages your plugin into a `.zip` artifact when you publish a new GitHub Release. It runs the signing command dynamically using a GitHub Secret (`KARCYTICS_PRIVATE_KEY`).

---

## 🔄 Migration Guide for V1 Developers

If you are an existing developer upgrading your plugins from Karcytics V1 to the new V2 ecosystem, you need to make a few critical changes:

1. **Adopt the Split-Manifest Architecture**:
   - V1 relied on a custom `manifest.json`. In V2, all configuration must be moved to standard Python `pyproject.toml`.
   - Your entry point must now be declared under `[tool.karcytics.plugin]` in your `pyproject.toml`.
2. **Move Source Code to `src/`**:
   - V1 allowed flat directory structures. V2 enforces a strict `src/` layout. Move all your Python logic into `src/` and ensure your entry point is resolvable.
3. **Generate Developer Keys**:
   - V1 had no cryptographic signing. You **must** run `karcytics-sdk init-identity` to generate an Ed25519 keypair.
4. **Sign Your Old Plugins**:
   - Before a V2 host can load your old plugin, you must run `karcytics-sdk sign <path_to_plugin>` to generate a `security.json`, `signature.bin`, and `trust_chain.json`.
5. **Use SDK UI Components**:
   - V2 introduces `karcytics_sdk.plugin.PluginBase`. You should subclass this instead of a raw `QWidget` for out-of-the-box styling and standardized event routing.

### Next Steps
Now that you understand the full lifecycle, you are ready to start building powerful bioinformatics plugins!
- **Want to build complex UI layouts?** Check out the [Developer Handbook](Dev_Handbook.md).
- **Need to understand the Cryptographic pipeline?** Read the [Security Guide](CI_CD_Guide.md).
- **Looking for API specifics?** Browse the [API Reference](api/index.md).

Happy hacking! 🎉

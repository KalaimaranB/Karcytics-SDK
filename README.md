# 🔌 Karcytics SDK

[![Documentation](https://img.shields.io/badge/docs-GitHub%20Pages-blueviolet?style=flat-square)](https://KalaimaranB.github.io/Karcytics-SDK/)
[![CI Build Status](https://img.shields.io/github/actions/workflow/status/KalaimaranB/Karcytics-SDK/test_and_lint.yml?branch=main&style=flat-square&label=CI%20build)](https://github.com/KalaimaranB/Karcytics-SDK/actions)
[![License](https://img.shields.io/badge/license-PolyForm%20Noncommercial-blue?style=flat-square)](https://github.com/KalaimaranB/Karcytics-SDK/blob/main/LICENSE)

The Software Development Kit (SDK) and Command-Line Interface (CLI) for building, validating, and signing plugins for the **Karcytics** desktop scientific suite.

---

## 🚀 Key Features

- **Decoupled Architecture**: Build and test PyQt6-based scientific plugins independently of the main desktop app.
- **Fail-Safe Dynamic Theme Fallbacks**: Visual components automatically load custom HSL-tailored colors when running standalone inside CI/CD test gates or external visualizers.
- **Merkle-Tree Cryptographic Integrity**: Built-in Ed25519 signing and verification tools to secure user environments against remote execution and tampering.
- **PyPI-Ready Packaging**: Complete declarative `pyproject.toml` config, built to publish natively under the `karcytics-sdk` package.

---

## 🛠️ Installation

Install the SDK directly into your plugin's virtual environment:

```bash
pip install karcytics-sdk
```

*(Or during development, install in editable mode):*
```bash
git clone https://github.com/KalaimaranB/Karcytics-SDK.git
cd Karcytics-SDK
pip install -e .
```

---

## 📦 Creating a Custom Karcytics Plugin

To build a valid plugin, implement the `KarcyticsPlugin` interface and declare your entrypoints.

### 1. `manifest.json`
Every plugin must include a manifest file in its root directory:
```json
{
  "id": "my_custom_plugin",
  "name": "My Custom Plugin",
  "version": "1.0.0",
  "author": "Dr. Kalaimaran",
  "description": "High-performance scientific analysis plugin.",
  "category": "analysis",
  "min_core_version": "1.0.0",
  "entrypoint": "plugin:MyPluginClass"
}
```

### 2. `plugin.py`
```python
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QLabel
from karcytics_sdk.core.interfaces import KarcyticsPlugin
from karcytics_sdk.ui import PrimaryButton


class MyPluginClass(KarcyticsPlugin):
    """A professional-grade Karcytics plugin."""

    def initialize(self) -> None:
        self.logger.info("Initializing custom plugin...")

    def create_panel(self, parent=None) -> QWidget:
        panel = QWidget(parent)
        layout = QVBoxLayout(panel)

        title = QLabel("Welcome to Custom Analysis Panel")
        btn = PrimaryButton("Execute Step")

        layout.addWidget(title)
        layout.addWidget(btn)
        return panel
```

---

## 🛡️ Cryptographic Trust Architecture

Karcytics implements a professional-grade **Chain of Trust** to protect laboratory environments:

```
[ Root Authority ] (Hardcoded Core Key)
       │
       ▼ (signs)
[ Developer Key ] (Dev Certificate)
       │
       ▼ (signs)
[ Plugin Manifest ] (Ed25519 Signature + Merkle-Tree Hashes)
```

### 1. Generate Your Cryptographic Identity
```bash
karcytics-sdk setup-identity
```
- Local Private Key: `~/.karcytics/dev_private_key.pem`
- Developer Certificate: `~/.karcytics/dev_cert.bin`

### 2. Sign Your Plugin payload
Calculates Merkle-hashes for all your files recursively, excludes development directories automatically, updates `manifest.json`, and writes `signature.bin`:
```bash
karcytics-sdk sign <path/to/plugin>
```

### 3. Modularity Compliance Check
Verify your plugin matches QA and security standards:
```bash
karcytics-sdk evaluate <path/to/plugin>
```

---

## 📘 Standalone Preview Support
Since the SDK decouples all theme components from the desktop core using robust try-except fallbacks, you can instantiate and preview components standalone in development:

```python
import sys
from PyQt6.QtWidgets import QApplication
from karcytics_sdk.ui import PrimaryButton, WizardPanel

app = QApplication(sys.argv)
widget = WizardPanel()  # Renders beautifully even without the main app!
widget.show()
sys.exit(app.exec())
```

---

## 📄 License
This project is free for academic and personal use under the [PolyForm Noncommercial License 1.0.0](https://polyformproject.org/licenses/noncommercial/1.0.0). Commercial use requires a separate paid license (not yet available).

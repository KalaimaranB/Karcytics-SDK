from pathlib import Path


def setup_scaffold_parser(subparsers):
    # Command: create-manifest
    manifest_parser = subparsers.add_parser("create-manifest", help="Bootstraps a fresh manifest.json for a plugin.")
    manifest_parser.add_argument("plugin_dir", type=str, help="Path to the plugin directory.")
    manifest_parser.add_argument("--id", type=str, help="Custom plugin ID (snake_case).")
    manifest_parser.add_argument("--name", type=str, help="Custom plugin display name.")
    manifest_parser.add_argument("--version", type=str, help="Custom plugin version.")
    manifest_parser.add_argument("--desc", type=str, help="Custom plugin description.")
    manifest_parser.set_defaults(func=create_manifest)

    # Command: bootstrap
    bootstrap_parser = subparsers.add_parser("bootstrap", help="Create a complete boilerplate plugin skeleton.")
    bootstrap_parser.add_argument("plugin_dir", type=str, help="Path to the plugin directory.")
    bootstrap_parser.set_defaults(func=bootstrap_plugin)

    # Command: init
    init_parser = subparsers.add_parser("init", help="Scaffold a fresh plugin repository.")
    init_parser.add_argument("plugin_name", type=str, help="Name of the new plugin.")
    init_parser.set_defaults(func=init_plugin)


def create_manifest(args) -> bool:
    """Interactive/Scriptable bootstrapping for a pyproject.toml config."""
    p_dir = Path(args.plugin_dir)
    p_dir.mkdir(parents=True, exist_ok=True)
    toml_path = p_dir / "pyproject.toml"

    if toml_path.exists():
        print(f"⚠️  pyproject.toml already exists at {toml_path}. Aborting to prevent overwrite.")
        return False

    # Gather inputs or default
    p_id = args.id or p_dir.name.lower().replace("-", "_").replace(" ", "_")
    p_name = args.name or p_dir.name.title()
    p_version = args.version or "1.0.0"
    p_description = args.desc or f"A high-performance Karcytics data plugin analyzing {p_name}."

    toml_content = f'''[project]
name = "{p_name}"
version = "{p_version}"
description = "{p_description}"
readme = "README.md"
requires-python = ">=3.11"
authors = [
  {{ name = "Developer Name" }}
]
dependencies = [
]

[project.optional-dependencies]
dev = [
    "pytest",
    "pytest-qt",
    "ruff",
    "mkdocs-material"
]

[tool.karcytics.plugin]
id = "{p_id}"
min_core_version = "1.4.9"
entry_point = "karcytics_plugins.{p_id}:initialize"
requires = ["task_scheduler", "logger", "event_bus"]
authors = [
  {{ name = "Developer Name", role = "Developer", permissions = ["read_workspace", "write_assets"] }}
]
'''
    with open(toml_path, "w", encoding="utf-8") as f:
        f.write(toml_content)

    print(f"🎉 Successfully created plugin configuration at: {toml_path}")
    return True


CLAUDE_SETTINGS_TEMPLATE = """{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "Edit|Write|MultiEdit",
        "hooks": [{"type": "command", "command": "uv run karcytics-sdk doctor --claude-hook"}]
      }
    ]
  }
}
"""


def bootstrap_plugin(args) -> bool:
    """Create a complete boilerplate plugin skeleton with documentation and source template."""
    p_dir = Path(args.plugin_dir)
    p_dir.mkdir(parents=True, exist_ok=True)

    # 1. Create subfolders
    (p_dir / "src").mkdir(parents=True, exist_ok=True)
    (p_dir / "docs").mkdir(parents=True, exist_ok=True)
    workflows_dir = p_dir / ".github" / "workflows"
    workflows_dir.mkdir(parents=True, exist_ok=True)

    # 2. Create manifest.json (pyproject.toml)
    import argparse

    create_args = argparse.Namespace(plugin_dir=args.plugin_dir, id=None, name=None, version=None, desc=None)
    create_manifest(create_args)

    # Let an agent working in the repo see what each edit breaks (docs/Doctor.md).
    claude_dir = p_dir / ".claude"
    claude_dir.mkdir(parents=True, exist_ok=True)
    (claude_dir / "settings.json").write_text(CLAUDE_SETTINGS_TEMPLATE, encoding="utf-8")

    # 3. Create a clean __init__.py boilerplate implementing AnalysisBase
    init_file = p_dir / "src" / "__init__.py"
    init_content = """from karcytics_sdk.plugin import AnalysisBase

class CustomAnalysisPlugin(AnalysisBase):
    \"\"\"Boilerplate analysis plugin demonstrating safe SDK interaction.\"\"\"

    def execute(self, workspace_context):
        \"\"\"Executes primary data processing workflow.

        Args:
            workspace_context: The host application environment and loaded data assets.
        \"\"\"
        self.logger.info("Executing custom boilerplate analysis workflow...")

        # Access workspace variables
        assets = workspace_context.get_assets()
        self.logger.info(f"Loaded {len(assets)} raw assets in current workspace.")

        # Complete work and publish progress
        self.publish_progress(100, "Boilerplate execution completed.")
        return {"status": "success", "processed_assets": len(assets)}
"""
    with open(init_file, "w", encoding="utf-8") as f:
        f.write(init_content)

    # 4. Create a README.md inside docs/
    readme_file = p_dir / "docs" / "01_getting_started.md"
    readme_content = f"""# Getting Started with {p_dir.name.title()}

Welcome to your freshly bootstrapped Karcytics plugin!

## Architecture
This plugin is developed using the Karcytics-SDK. It exposes a single data analysis pipeline extending `AnalysisBase`.

## Getting Started
1. Edit `src/__init__.py` to implement your custom data algorithms.
2. Maintain your documentation under the `docs/` folder for local integration with the Karcytics Help Center.
3. Sign your plugin before loading using:
   ```bash
   karcytics-sdk sign .
   ```
"""
    with open(readme_file, "w", encoding="utf-8") as f:
        f.write(readme_content)

    # 5. Create GitHub Actions workflows
    ci_file = workflows_dir / "ci.yml"
    ci_content = """name: CI — Tests & Lint

on:
  push:
    branches: [main, develop]
  pull_request:

jobs:
  test:
    runs-on: ${{ matrix.os }}
    strategy:
      matrix:
        os: [ubuntu-latest, macos-latest, windows-latest]
    steps:
      - uses: actions/checkout@v4
      - name: Install uv
        uses: astral-sh/setup-uv@v2
        with:
          version: "latest"
      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - name: Install system Qt deps (Ubuntu)
        if: matrix.os == 'ubuntu-latest'
        run: sudo apt-get update && sudo apt-get install -y libegl1 libxkbcommon-x11-0 libxcb-cursor0
      - name: Install dependencies
        run: |
          uv sync
          uv pip install git+https://github.com/KalaimaranB/Karcytics-SDK.git
        shell: bash
      - name: Run tests
        run: uv run pytest tests/ -v
        env:
          QT_QPA_PLATFORM: offscreen
        shell: bash

  lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Install uv
        uses: astral-sh/setup-uv@v2
        with:
          version: "latest"
      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - run: uv pip install --system ruff
      - run: ruff check src/ tests/
"""
    with open(ci_file, "w", encoding="utf-8") as f:
        f.write(ci_content)

    deploy_docs_file = workflows_dir / "deploy-docs.yml"
    deploy_docs_content = """name: Deploy Documentation

on:
  push:
    branches:
      - main
  workflow_dispatch:

permissions:
  contents: write

jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Configure Git Credentials
        run: |
          git config user.name github-actions[bot]
          git config user.email 41898282+github-actions[bot]@users.noreply.github.com
      - name: Install uv
        uses: astral-sh/setup-uv@v2
        with:
          version: "latest"
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - run: echo "cache_id=$(date --utc '+%V')" >> $GITHUB_ENV
      - uses: actions/cache@v4
        with:
          key: mkdocs-material-${{ env.cache_id }}
          path: .cache
          restore-keys: |
            mkdocs-material-
      - name: Install dependencies
        run: |
          uv pip install --system mkdocs-material "mkdocstrings[python]"
      - name: Build and Deploy
        env:
          PYTHONPATH: src
        run: mkdocs gh-deploy --force
"""
    with open(deploy_docs_file, "w", encoding="utf-8") as f:
        f.write(deploy_docs_content)

    release_file = workflows_dir / "release.yml"
    release_content = """name: Auto-Release Plugin

on:
  push:
    branches:
      - main

env:
  FORCE_JAVASCRIPT_ACTIONS_TO_NODE24: true

jobs:
  check_version:
    name: Check Version Bump
    runs-on: ubuntu-latest
    outputs:
      changed: ${{ steps.check.outputs.changed }}
      version: ${{ steps.check.outputs.version }}
      plugin_id: ${{ steps.check.outputs.plugin_id }}
      release_notes: ${{ steps.check.outputs.release_notes }}
    steps:
      - name: Checkout Code
        uses: actions/checkout@v4
      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - name: Check Version
        id: check
        run: |
          python -c "
import sys
if sys.version_info >= (3, 11):
    import tomllib as toml
else:
    import toml

with open('pyproject.toml', 'rb') as f:
    data = toml.load(f)

project = data.get('project', {})
plugin = data.get('tool', {}).get('karcytics', {}).get('plugin', {})

plugin_id = plugin.get('id', '')
version = project.get('version', '')
release_notes = plugin.get('release_notes', 'No release notes provided.')

import os
import uuid

with open(os.environ['GITHUB_OUTPUT'], 'a') as f:
    f.write(f'plugin_id={plugin_id}\\n')
    f.write(f'version={version}\\n')

    eof_marker = str(uuid.uuid4())
    f.write(f'release_notes<<{eof_marker}\\n')
    f.write(f'{release_notes}\\n')
    f.write(f'{eof_marker}\\n')
"
          VERSION=$(python -c "import tomllib; print(tomllib.load(open('pyproject.toml', 'rb'))['project']['version'])")
          TAG_NAME="v$VERSION"

          if git ls-remote --tags origin | grep -q "refs/tags/$TAG_NAME"; then
            echo "Version $VERSION already released. Skipping build."
            echo "changed=false" >> $GITHUB_OUTPUT
          else
            echo "New version $VERSION detected. Proceeding with release."
            echo "changed=true" >> $GITHUB_OUTPUT
          fi

  release:
    name: Evaluate, Sign & Release
    needs: check_version
    if: needs.check_version.outputs.changed == 'true'
    runs-on: ubuntu-latest
    permissions:
      contents: write
    steps:
      - name: Checkout Code
        uses: actions/checkout@v4
      - name: Tag Repository
        run: |
          git config user.name "github-actions[bot]"
          git config user.email "github-actions[bot]@users.noreply.github.com"
          TAG_NAME="v${{ needs.check_version.outputs.version }}"
          git tag $TAG_NAME
          git push origin $TAG_NAME
      - name: Install uv
        uses: astral-sh/setup-uv@v2
        with:
          version: "latest"
      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - name: Install Qt System Dependencies
        run: sudo apt-get update && sudo apt-get install -y libegl1 libxkbcommon-x11-0 libxcb-cursor0
      - name: Install Karcytics SDK
        run: |
          uv pip install --system git+https://github.com/KalaimaranB/Karcytics-SDK.git
      - name: SDK Evaluate Plugin
        run: |
          karcytics-sdk evaluate .
        env:
          QT_QPA_PLATFORM: offscreen
      - name: Execute Project Signing
        run: |
          karcytics-sdk project-sign .
        env:
          KARCYTICS_PROJECT_PRIVATE_KEY: ${{ secrets.KARCYTICS_PROJECT_PRIVATE_KEY }}
          QT_QPA_PLATFORM: offscreen
      - name: Build Release ZIP
        run: |
          PLUGIN_ID="${{ needs.check_version.outputs.plugin_id }}"
          NEW_VERSION="${{ needs.check_version.outputs.version }}"
          ZIP_NAME="${PLUGIN_ID}_v${NEW_VERSION}.zip"
          zip -r "$ZIP_NAME" . \\
            --exclude "*.git*" \\
            --exclude "*tests/*" \\
            --exclude "*__pycache__/*" \\
            --exclude "*.venv/*" \\
            --exclude "*.plugin_venv/*" \\
            --exclude "*.DS_Store" \\
            --exclude "*.pem" \\
            --exclude "*.key" \\
            --exclude "*.pytest_cache/*" \\
            --exclude "*.github/*"
      - name: Create GitHub Release
        uses: softprops/action-gh-release@v2
        with:
          tag_name: "v${{ needs.check_version.outputs.version }}"
          name: "${{ needs.check_version.outputs.plugin_id }} v${{ needs.check_version.outputs.version }}"
          body: |
            ### Release Notes
            ${{ needs.check_version.outputs.release_notes }}

            ---
            *Auto-generated changes below:*
          generate_release_notes: true
          files: "${{ needs.check_version.outputs.plugin_id }}_v${{ needs.check_version.outputs.version }}.zip"
      - name: Checkout Karcytics-Distribution
        uses: actions/checkout@v4
        with:
          repository: KalaimaranB/Karcytics-Distribution
          token: ${{ secrets.DIST_PAT }}
          path: dist-repo
      - name: Update registry.json
        run: |
          cd dist-repo
          python3 - <<'EOF2'
          import json, sys
          with open("registry.json") as f:
              reg = json.load(f)
          plugin_id = "${{ needs.check_version.outputs.plugin_id }}"
          new_version = "${{ needs.check_version.outputs.version }}"
          tag_name = f"v{new_version}"
          zip_name = f"{plugin_id}_{tag_name}.zip"
          download_url = f"https://github.com/${{ github.repository }}/releases/download/{tag_name}/{zip_name}"
          if plugin_id in reg.get("plugins", {}):
              reg["plugins"][plugin_id]["version"] = new_version
              reg["plugins"][plugin_id]["download_url"] = download_url
              with open("registry.json", "w") as f:
                  json.dump(reg, f, indent=2)
              print(f"Updated {plugin_id} in registry.json")
          else:
              print(f"Error: {plugin_id} not found in registry.json")
              sys.exit(1)
          EOF2
      - name: Open Registry Update PR
        uses: peter-evans/create-pull-request@v6
        with:
          token: ${{ secrets.DIST_PAT }}
          path: dist-repo
          branch: "auto/update-${{ needs.check_version.outputs.plugin_id }}-v${{ needs.check_version.outputs.version }}"
          title: "chore: bump ${{ needs.check_version.outputs.plugin_id }} to v${{ needs.check_version.outputs.version }}"
          commit-message: "chore: bump ${{ needs.check_version.outputs.plugin_id }} to ${{ needs.check_version.outputs.version }}"
          body: |
            ## Automated Registry Update
            Plugin **`${{ needs.check_version.outputs.plugin_id }}`** was released at tag `v${{ needs.check_version.outputs.version }}`.
            ### Developer Release Notes
            > ${{ needs.check_version.outputs.release_notes }}

            This PR updates `registry.json` with the latest version.
            > ⚠️ **Review before merging:** Verify the download URL is correct and the ZIP is valid.
          add-paths: registry.json
"""
    with open(release_file, "w", encoding="utf-8") as f:
        f.write(release_content)

    print(f"\n🚀 Successfully bootstrapped boilerplate plugin at: {p_dir}")
    print("Structure Created:")
    print("  ├── pyproject.toml (Configuration)")
    print("  ├── src/")
    print("  │   └── __init__.py  (Core plugin logic)")
    print("  ├── docs/")
    print("  │   └── 01_getting_started.md (Documentation)")
    print("  └── .github/workflows/")
    print("      └── ci.yml, deploy-docs.yml, release.yml")
    print("\nGet started by running:")
    print(f'  cd "{p_dir}" && karcytics-sdk init-identity && karcytics-sdk sign .')
    return True


def init_plugin(args) -> bool:
    """Scaffold a fresh plugin repository with src/ layout and plugin.toml."""
    print(f"Scaffolding new plugin '{args.plugin_name}' with karcytics-sdk init...")
    print("Note: Scaffolded for Phase 1.")
    return True

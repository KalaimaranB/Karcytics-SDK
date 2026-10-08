# 📦 Karcytics SDK Release and PyPI Publication Guide

This guide provides step-by-step instructions on how to publish the **Karcytics SDK** (`karcytics-sdk`) to PyPI, set up automated CI/CD releases, and push the local codebase to your newly created GitHub repository.

---

## 🚀 Step 1: Push Code to GitHub

Your repository at `https://github.com/KalaimaranB/Karcytics-SDK` is currently empty. Follow these steps to push your local SDK files:

1. **Open your terminal** and navigate to the SDK directory:
   ```bash
   cd "/Users/kalaimaranbalasothy/GitHub Projects/Karcytics-SDK"
   ```

2. **Initialize Git** (if not already done) and set the remote:
   ```bash
   git init
   git remote add origin https://github.com/KalaimaranB/Karcytics-SDK.git
   ```

3. **Stage and commit all files**:
   ```bash
   git add .
   git commit -m "Initial commit: decouping Karcytics-SDK from Core application"
   ```

4. **Push to GitHub**:
   ```bash
   git branch -M main
   git push -u origin main
   ```

---

## 🔒 Step 2: Set Up PyPI Trusted Publishing (OIDC) - Recommended

The SDK includes a `.github/workflows/publish.yml` file pre-configured for **Trusted Publishing (OIDC)**. This is the most secure method recommended by PyPI as it uses short-lived tokens instead of storing raw API keys in GitHub secrets.

### How to configure it:
1. Log into your [PyPI Account](https://pypi.org/).
2. Navigate to **Account Settings** ➔ **Publishing**.
3. Under **Add a new publisher**, select **GitHub**.
4. Enter the following details:
   - **Owner:** `KalaimaranB`
   - **Repository:** `Karcytics-SDK`
   - **Workflow name:** `publish.yml`
   - **Environment:** `release` (or leave empty if you want it to trigger on any tag)
5. Click **Add Publisher**.

Once added, any time you push a tag matching `v*` (e.g., `v1.0.0`), GitHub Actions will securely request a short-lived token from PyPI and publish the package automatically!

---

## 🛠️ Step 3: Trigger an Automated GitHub Release

To release a new version of the SDK via GitHub Actions:

1. Update the version inside `pyproject.toml` (e.g., `version = "1.0.0"`).
2. Commit and push the changes:
   ```bash
   git add pyproject.toml
   git commit -m "Bump version to 1.0.0"
   git push origin main
   ```
3. Tag the commit and push the tags:
   ```bash
   git tag v1.0.0
   git push origin --tags
   ```

This will trigger the `.github/workflows/publish.yml` workflow, building the wheels and publishing them straight to PyPI!

---

## 🖥️ Step 4: Manual PyPI Upload (Alternative)

If you prefer to build and upload the package manually from your local machine, follow these steps:

1. **Activate your virtual environment and install build tools**:
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install --upgrade pip build twine
   ```

2. **Build the source distribution (sdist) and binary wheels**:
   ```bash
   python3 -m build
   ```
   This will generate a `dist/` directory containing files like `karcytics_sdk-1.0.0-py3-none-any.whl` and `karcytics-sdk-1.0.0.tar.gz`.

3. **Verify the build package contents**:
   ```bash
   twine check dist/*
   ```

4. **Upload to PyPI**:
   ```bash
   twine upload dist/*
   ```
   *(You will be prompted to enter your PyPI username `__token__` and your API Token as the password.)*

---

## 📝 SDK Folder Structure Summary

- **`src/karcytics_sdk/`**: The core codebase containing:
  - `core/`: Verification trust managers, local override registries, and interface models.
  - `ui/`: PySide6 reusable visual components and wizard form guides.
  - `utils/`: High performance IO helpers.
  - `contrib/`: Heavy deep-learning and image processing wrappers.
  - `sdk_cli.py`: The `karcytics-sdk` command line engine.
- **`pyproject.toml`**: Package metadata, entrypoints, and standard PyPI dependencies.
- **`.gitignore`**: Strict exclusion profiles to prevent local keys or runtime noise from entering the repo.
- **`README.md`**: Standard developer installation and basic usage commands.

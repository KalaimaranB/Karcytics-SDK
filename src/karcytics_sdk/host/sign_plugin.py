"""Developer Utility for Signing Karcytics Plugins (Unified V2 Engine).

Commands:
    init:          Generate a new Ed25519 developer key pair.
    sign:          Calculate integrity hashes and sign a plugin (split-manifest).
    project-sign:  Co-sign a plugin's security ledger as the institutional Project CI runner.
    registry:      Export the JSON snippet for the central registry.
"""

import datetime
import hashlib
import json
import logging
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

from karcytics_sdk.plugin.manifest_parser import ManifestParser
from karcytics_sdk.plugin.security_parser import SecurityParser, SecurityValidationError


@dataclass
class TrustLink:
    """A single link in the trust chain (Issuer -> Subject)."""

    subject_name: str
    subject_pub: str  # Hex encoded Ed25519 public key
    issuer_name: str
    signature: str  # Hex encoded signature

    def to_dict(self) -> dict:
        return {
            "subject_name": self.subject_name,
            "subject_pub": self.subject_pub,
            "issuer_name": self.issuer_name,
            "signature": self.signature,
        }


@dataclass
class TrustChain:
    """The full chain of trust for a plugin."""

    links: list[TrustLink]

    def to_json(self) -> str:
        return json.dumps([link.to_dict() for link in self.links], indent=4)

    @classmethod
    def from_json(cls, json_str: str) -> "TrustChain":
        data = json.loads(json_str)
        links = [TrustLink(**item) for item in data]
        return cls(links=links)

    @classmethod
    def from_file(cls, path: Path) -> Optional["TrustChain"]:
        if not path.exists():
            return None
        try:
            with open(path) as f:
                return cls.from_json(f.read())
        except Exception:
            return None


# Configure basic logging for CLI
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger("karcytics-signer")

MANDATORY_EXTENSIONS = {".py", ".pyw", ".json", ".yml", ".yaml", ".fcs", ".png", ".jpg"}
IGNORE_LIST = {
    ".DS_Store",
    "Thumbs.db",
    "__pycache__",
    ".git",
    ".github",
    ".vscode",
    ".idea",
    ".pytest_cache",
    ".venv",
    "venv",
    "cache",
    "results",
    "temp",
    "logs",
    "output",
    "dist",
    "build",
    "signature.bin",
    "project_signature.bin",
    "trust_chain.json",
    "pyproject.toml",
    "security.json",
    "dev_cert.bin",
}


_PLUGIN_TABLE_RE = re.compile(r"^\[tool\.karcytics\.plugin\]\s*(#.*)?$")
_TABLE_RE = re.compile(r"^\s*\[")
_RELEASED_RE = re.compile(r"^released_at\s*=")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _release_fields(text: str) -> tuple[str | None, str | None]:
    """(``[project] version``, ``[tool.karcytics.plugin] released_at``) from pyproject.toml text."""
    import tomllib

    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return None, None
    version = data.get("project", {}).get("version")
    released = data.get("tool", {}).get("karcytics", {}).get("plugin", {}).get("released_at")
    return (version if isinstance(version, str) else None, released if isinstance(released, str) else None)


def default_released_at(manifest_file: Path, today: str | None = None) -> str:
    """The date ``sign`` stamps when none is given.

    Keeps the existing ``released_at`` while the version is the one already committed (git
    ``HEAD``), so re-signing for a docs or config change doesn't move a release's date. A new
    version, a first signing, or no git history gets today.
    """
    today = today or datetime.date.today().isoformat()
    version, released = _release_fields(manifest_file.read_text(encoding="utf-8"))
    if not version or not released or not _DATE_RE.match(released):
        return today
    repo = manifest_file.resolve().parent
    try:
        head = subprocess.run(
            ["git", "show", "HEAD:./pyproject.toml"], cwd=repo, capture_output=True, text=True, timeout=10, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return today
    if head.returncode != 0:
        return today
    head_version, _ = _release_fields(head.stdout)
    return released if head_version == version else today


def stamp_released_at(manifest_file: Path, released_at: str) -> None:
    """Write ``released_at = "YYYY-MM-DD"`` into the ``[tool.karcytics.plugin]`` table.

    The Hub compares this date with a license's update period. It lives in pyproject.toml, so the
    signed ``manifest_hash`` covers it, and the Plugin Store (which reads each plugin's
    pyproject.toml) sees the same date. An existing value in that table is replaced; nothing else
    in the file changes.
    """
    if not _DATE_RE.match(released_at):
        raise ValueError(f"released_at must be YYYY-MM-DD, got {released_at!r}")
    datetime.date.fromisoformat(released_at)  # rejects impossible dates
    raw = manifest_file.read_bytes().decode("utf-8")
    newline = "\r\n" if "\r\n" in raw else "\n"
    lines = raw.splitlines()
    header = next((i for i, line in enumerate(lines) if _PLUGIN_TABLE_RE.match(line.strip())), None)
    if header is None:
        raise ValueError(f"{manifest_file} has no [tool.karcytics.plugin] table")
    end = next((i for i in range(header + 1, len(lines)) if _TABLE_RE.match(lines[i])), len(lines))
    entry = f'released_at = "{released_at}"'
    existing = next((i for i in range(header + 1, end) if _RELEASED_RE.match(lines[i])), None)
    if existing is not None:
        lines[existing] = entry
    else:
        lines.insert(header + 1, entry)
    manifest_file.write_bytes((newline.join(lines) + newline).encode("utf-8"))


class PluginSigner:
    def __init__(self, custom_dev_dir: Path | None = None):
        self.dev_dir = custom_dev_dir if custom_dev_dir else (Path.home() / ".karcytics" / "dev_keys")
        self.private_key_path = self.dev_dir / "private.key"
        self.public_key_path = self.dev_dir / "public.pub"
        self.delegation_path = self.dev_dir / "delegation.json"  # Your credentials from authority

    def init_identity(self):
        """Generates a new Ed25519 identity."""
        if self.private_key_path.exists():
            logger.info("Identity already exists. Delete ~/.karcytics/dev_keys/ to regenerate.")
            return

        self.dev_dir.mkdir(parents=True, exist_ok=True)
        private_key = ed25519.Ed25519PrivateKey.generate()
        public_key = private_key.public_key()

        # Save Private Key (PKCS8)
        with open(self.private_key_path, "wb") as f:
            f.write(
                private_key.private_bytes(
                    encoding=serialization.Encoding.PEM,
                    format=serialization.PrivateFormat.PKCS8,
                    encryption_algorithm=serialization.NoEncryption(),
                )
            )

        # Save Public Key (Raw Bytes for the Registry)
        with open(self.public_key_path, "wb") as f:
            f.write(public_key.public_bytes(encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw))

        logger.info("Identity generated successfully.")
        logger.info(f"Private Key: {self.private_key_path}")
        logger.info(f"Public Key:  {self.public_key_path}")
        logger.warning("Keep your private.key SAFE and SECRET!")

    def load_private_key(self) -> ed25519.Ed25519PrivateKey:
        if not self.private_key_path.exists():
            raise FileNotFoundError("Developer identity not found. Run 'init' first.")
        with open(self.private_key_path, "rb") as f:
            key = serialization.load_pem_private_key(f.read(), password=None)
            if not isinstance(key, ed25519.Ed25519PrivateKey):
                raise TypeError("Key is not a valid Ed25519PrivateKey")
            return key

    def sign_plugin(self, plugin_path: Path, released_at: str | None = None):  # noqa: C901, PLR0915
        """Generates security.json with manifest binding hash and creates signature.bin.

        Stamps ``released_at`` into pyproject.toml first, so the signed manifest carries the
        release date the Hub checks against a license's update period. Default: today for a new
        version, otherwise the date already there (see ``default_released_at``).
        """
        private_key = self.load_private_key()
        public_key = private_key.public_key()

        manifest_file = plugin_path / "pyproject.toml"
        if not manifest_file.exists():
            raise FileNotFoundError(f"pyproject.toml not found in {plugin_path}")
        stamp_released_at(manifest_file, released_at or default_released_at(manifest_file))

        # Parse & Validate manifest using new Split-Manifest strict rules
        parser = ManifestParser()
        try:
            manifest = parser.parse_file(manifest_file)
        except Exception as e:
            raise ValueError(f"Failed manifest parsing: {e}") from e

        plugin_id = manifest.get("id")
        if not plugin_id:
            raise ValueError("Plugin ID is required in manifest.")

        logger.info(f"Hashing files for {plugin_id}...")
        custom_excl = {e.rstrip("/") for e in (manifest.get("custom_exclusions") or [])}
        active_ignore = IGNORE_LIST | custom_excl
        hashes = {}
        for rel_path in self._discover_files(plugin_path):
            path_parts = rel_path.split("/")
            if any(part in active_ignore for part in path_parts):
                continue

            if any(rel_path.endswith(ext) for ext in MANDATORY_EXTENSIONS):
                hashes[rel_path] = self._hash_file(plugin_path / rel_path)

        # Calculate Manifest Hash Binding (Normalize line endings for cross-platform)
        try:
            text = manifest_file.read_text(encoding="utf-8")
            manifest_bytes = text.replace("\r\n", "\n").encode("utf-8")
        except UnicodeDecodeError:
            manifest_bytes = manifest_file.read_bytes()
        manifest_hash = hashlib.sha256(manifest_bytes).hexdigest()

        # Build security.json (Support custom exclusions if specified in the manifest)
        security_data = {
            "security_version": 1,
            "plugin_id": plugin_id,
            "manifest_hash": manifest_hash,
            "exclusions": manifest.get("custom_exclusions") or [],
            "hashes": hashes,
        }

        # Write security.json
        security_file = plugin_path / "security.json"
        with open(security_file, "w", encoding="utf-8") as f:
            json.dump(security_data, f, indent=4)

        # Sign security.json canonical bytes (preventing keys sorting/whitespaces variances)
        canonical_bytes = json.dumps(security_data, sort_keys=True, separators=(",", ":")).encode("utf-8")
        signature = private_key.sign(canonical_bytes)

        # Write signature.bin
        with open(plugin_path / "signature.bin", "wb") as f:
            f.write(signature)

        # Write trust_chain.json
        pub_bytes = public_key.public_bytes(encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw)

        authors = manifest.get("authors", [])
        dev_name = authors[0].get("name", "Unknown Developer") if authors else "Unknown Developer"

        dev_link = TrustLink(
            subject_name=dev_name,
            subject_pub=pub_bytes.hex(),
            issuer_name="Unknown",
            signature="0" * 128,
        )

        links = [dev_link]

        # Add delegations if present
        if self.delegation_path.exists():
            try:
                parent_chain = TrustChain.from_file(self.delegation_path)
                if parent_chain:
                    dev_link.issuer_name = parent_chain.links[0].subject_name
                    links = parent_chain.links
            except Exception as e:
                logger.warning(f"Failed to load delegation chain: {e}")

        chain = TrustChain(links=links)
        with open(plugin_path / "trust_chain.json", "w") as f:
            f.write(chain.to_json())

        logger.info(f"Successfully signed {plugin_id}")
        logger.info("Generated: signature.bin, trust_chain.json")

    def project_sign_plugin(  # noqa: PLR0915
        self, plugin_path: Path, project_private_key_pem: bytes, delegation_path: Path | None = None
    ):
        """Verifies developer signatures and file integrity, then co-signs security.json as the Project CI runner."""
        security_file = plugin_path / "security.json"
        signature_file = plugin_path / "signature.bin"
        trust_file = plugin_path / "trust_chain.json"
        manifest_file = plugin_path / "pyproject.toml"

        if not security_file.exists() or not signature_file.exists() or not trust_file.exists():
            msg = "Developer signature (signature.bin) or security ledger is missing. Rejecting pipeline."
            logger.error(msg)
            raise SecurityValidationError(msg)

        try:
            # 1. Parse security.json
            sec_parser = SecurityParser()
            security_data = sec_parser.parse_file(str(security_file))

            # 2. Verify Manifest cryptographic binding hash
            sec_parser.verify_manifest_binding(manifest_file, security_data)

            # 3. Verify developer Ed25519 signature
            chain = TrustChain.from_file(trust_file)
            if not chain or not chain.links:
                raise SecurityValidationError("Invalid trust chain file.")

            dev_pub_hex = chain.links[0].subject_pub
            dev_pub_key = ed25519.Ed25519PublicKey.from_public_bytes(bytes.fromhex(dev_pub_hex))

            dev_sig = signature_file.read_bytes()
            canonical_bytes = json.dumps(security_data, sort_keys=True, separators=(",", ":")).encode("utf-8")
            dev_pub_key.verify(dev_sig, canonical_bytes)

            # 4. Audit all file hashes to prevent post-signature developer tampering in pipeline
            for rel_path, expected_hash in security_data["hashes"].items():
                file_path = plugin_path / rel_path
                if not file_path.exists() or self._hash_file(file_path) != expected_hash:
                    raise SecurityValidationError(f"File {rel_path} integrity hash has changed.")

        except Exception as e:
            logger.error(f"Security validation failed before project-signing. Re-check file integrity. Error: {e}")
            # This is the release pipeline's last chance to reject a plugin whose
            # shipped files don't match what was actually signed — swallowing it
            # here (rather than raising) let a mismatched release through
            # undetected before, with the CLI still exiting 0.
            raise

        # 5. Load Project CI private key
        try:
            project_private_key = serialization.load_pem_private_key(project_private_key_pem, password=None)
            if not isinstance(project_private_key, ed25519.Ed25519PrivateKey):
                raise TypeError("Project key is not a valid Ed25519PrivateKey")
        except Exception as e:
            logger.error(f"Failed to load project private key: {e}")
            raise

        # 6. Sign security.json canonical bytes
        project_signature = project_private_key.sign(canonical_bytes)

        # 7. Write project_signature.bin
        with open(plugin_path / "project_signature.bin", "wb") as f:
            f.write(project_signature)

        # 8. Append Project Link to trust_chain.json if provided
        if delegation_path and delegation_path.exists():
            runner_chain = TrustChain.from_file(delegation_path)
            if runner_chain and runner_chain.links:
                chain.links.extend(runner_chain.links)
                with open(trust_file, "w") as f:
                    f.write(chain.to_json())
                logger.info(f"Successfully appended CI runner delegation to {security_data['plugin_id']}.")
            else:
                msg = f"Failed to load valid CI runner delegation from {delegation_path}"
                logger.error(msg)
                raise SecurityValidationError(msg)
        else:
            logger.info("No delegation file provided for CI runner; assuming runner is pre-registered in trust chain.")

        logger.info(f"Successfully applied Project signature to {security_data['plugin_id']}.")

    def delegate_identity(self, subject_pub_file: Path, subject_name: str, authority_key_path: Path | None = None):
        """Signs another developer's public key using an Authority key."""
        if authority_key_path:
            with open(authority_key_path, "rb") as f:
                key = serialization.load_pem_private_key(f.read(), password=None)
                if not isinstance(key, ed25519.Ed25519PrivateKey):
                    raise TypeError("Authority key is not a valid Ed25519PrivateKey")
                private_key = key
            with open(
                authority_key_path.with_suffix(".pub")
                if authority_key_path.with_suffix(".pub").exists()
                else self.public_key_path,
                "rb",
            ) as f:
                auth_name = "Karcytics Core Authority" if "root" in str(authority_key_path).lower() else "Authority"
        else:
            private_key = self.load_private_key()
            auth_name = "Me"

        if not subject_pub_file.exists():
            logger.error(f"Subject public key file not found: {subject_pub_file}")
            return

        with open(subject_pub_file, "rb") as f:
            sub_pub_bytes = f.read()
            if len(sub_pub_bytes) != 32:  # noqa: PLR2004
                try:
                    pub = serialization.load_pem_public_key(sub_pub_bytes)
                    sub_pub_bytes = pub.public_bytes(
                        encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw
                    )
                except Exception:
                    logger.error("Invalid public key format.")
                    return

        signature = private_key.sign(sub_pub_bytes)

        new_link = TrustLink(
            subject_name=subject_name,
            subject_pub=sub_pub_bytes.hex(),
            issuer_name=auth_name,
            signature=signature.hex(),
        )

        links = [new_link]

        if not authority_key_path and self.delegation_path.exists():
            parent_chain = TrustChain.from_file(self.delegation_path)
            if parent_chain:
                new_link.issuer_name = parent_chain.links[0].subject_name
                links.extend(parent_chain.links)

        chain = TrustChain(links=links)
        output_file = Path(f"delegation_{subject_name.lower().replace(' ', '_')}.json")
        with open(output_file, "w") as f:
            f.write(chain.to_json())

        logger.info(f"Delegation file created: {output_file}")

    def _discover_files(self, plugin_path: Path) -> list[str]:
        """Return the plugin's candidate files as forward-slash-separated relative paths.

        Prefers `git ls-files` so anything gitignored - a venv, local
        editor/tool config, credentials, whatever else happens to exist on
        whoever's machine runs `sign` - can never get baked into the signed
        security ledger just because it was sitting on disk. IGNORE_LIST and
        a plugin's own `custom_exclusions` still apply on top of this, for
        names that are excluded by convention rather than actually gitignored.

        Falls back to a raw filesystem walk (the old behavior) only when
        plugin_path isn't inside a git repository at all.
        """
        try:
            result = subprocess.run(  # noqa: S603
                ["git", "-C", str(plugin_path), "ls-files"],  # noqa: S607
                capture_output=True,
                text=True,
                check=True,
            )
            return sorted(line for line in result.stdout.splitlines() if line)
        except (subprocess.CalledProcessError, FileNotFoundError):
            paths = []
            for root, _dirs, files in os.walk(plugin_path):
                for file in files:
                    rel = os.path.relpath(os.path.join(root, file), plugin_path)
                    paths.append(rel.replace(os.sep, "/"))
            return sorted(paths)

    def _hash_file(self, path: Path) -> str:
        hasher = hashlib.sha256()

        # Normalize line endings for text files to prevent cross-platform signature mismatches
        text_extensions = {".py", ".pyw", ".json", ".yml", ".yaml", ".toml", ".md", ".txt"}
        if path.suffix.lower() in text_extensions:
            try:
                text = path.read_text(encoding="utf-8")
                hasher.update(text.replace("\r\n", "\n").encode("utf-8"))
                return hasher.hexdigest()
            except UnicodeDecodeError:
                pass

        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                hasher.update(chunk)
        return hasher.hexdigest()

    def print_registry_entry(self):
        """Prints the JSON block for the registry."""
        if not self.public_key_path.exists():
            logger.error("No identity found. Run 'init' first.")
            return

        with open(self.public_key_path, "rb") as f:
            pub_hex = f.read().hex()

        entry = {"developer_id": "Your-GitHub-Username", "public_key": pub_hex}
        print("\n--- COPY THIS TO YOUR registry.json ---")
        print(json.dumps(entry, indent=4))
        print("---------------------------------------")


def sign_plugin(plugin_path: Path, private_key_path: Path | None = None, cert_path: Path | None = None):
    """Wrapper to maintain legacy compatibility with V1 import signatures."""
    signer = PluginSigner()
    if private_key_path:
        signer.private_key_path = private_key_path
    signer.sign_plugin(plugin_path)


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Karcytics Plugin Signer")
    subparsers = parser.add_subparsers(dest="command")

    init_parser = subparsers.add_parser("init")
    init_parser.add_argument("--out", help="Custom directory to output keys (optional)")

    sign_parser = subparsers.add_parser("sign")
    sign_parser.add_argument("path", help="Path to plugin folder")

    project_sign_parser = subparsers.add_parser("project-sign")
    project_sign_parser.add_argument("path", help="Path to plugin folder")
    project_sign_parser.add_argument(
        "--key-env",
        default="KARCYTICS_PROJECT_PRIVATE_KEY",
        help="Environment variable containing Project private key PEM",
    )
    project_sign_parser.add_argument(
        "--delegation",
        help="Path to runner delegation file to append to the trust chain (optional)",
    )

    delegate_parser = subparsers.add_parser("delegate")
    delegate_parser.add_argument("pub_path", help="Path to researcher's public.pub")
    delegate_parser.add_argument("name", help="Researcher's name")
    delegate_parser.add_argument("--authority", help="Path to authority private key (optional)")

    subparsers.add_parser("registry")

    args = parser.parse_args()
    signer = PluginSigner(Path(args.out) if hasattr(args, "out") and args.out else None)

    if args.command == "init":
        signer.init_identity()
    elif args.command == "sign":
        signer.sign_plugin(Path(args.path))
    elif args.command == "project-sign":
        pem_str = os.environ.get(args.key_env)
        if not pem_str:
            logger.error(f"Project signing key not found in env: {args.key_env}")
            return
        delegation_path = Path(args.delegation) if args.delegation else None
        signer.project_sign_plugin(Path(args.path), pem_str.encode("utf-8"), delegation_path)
    elif args.command == "delegate":
        signer.delegate_identity(Path(args.pub_path), args.name, Path(args.authority) if args.authority else None)
    elif args.command == "registry":
        signer.print_registry_entry()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()

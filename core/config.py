"""Configuration management for Blender AI Sidebar.

Zero Blender (bpy) dependencies. Pure Python.
Resolves configuration using the priority chain:
  1. Environment variables
  2. User configuration file (config.json)
  3. Safe defaults (Localhost / Ollama)
"""

from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

DEFAULT_BASE_URL = "http://localhost:11434/v1"
DEFAULT_MODEL = "llama3.1:latest"
DEFAULT_TIMEOUT_SECONDS = 30.0
CURRENT_CONFIG_VERSION = 1


def mask_api_key(key: Optional[str]) -> str:
    """Mask an API key for safe display in UI or logs."""
    if not key:
        return ""
    stripped = key.strip()
    if len(stripped) <= 8:
        return "********"
    return f"{stripped[:4]}...{stripped[-4:]}"


def is_local_endpoint(url: str) -> bool:
    """Check if the provided URL points to a local loopback address."""
    if not url:
        return False
    lower = url.lower()
    # Simple, robust check without external or network modules
    return (
        "://localhost" in lower
        or "://127.0.0.1" in lower
        or "://[::1]" in lower
        or lower.startswith("localhost")
        or lower.startswith("127.0.0.1")
    )


def is_network_allowed(url: str, online_access_enabled: bool) -> Tuple[bool, str]:
    """Validate whether network requests to the given URL are allowed.

    Localhost endpoints are permitted regardless of online_access.
    Remote endpoints require online_access to be True.
    """
    if is_local_endpoint(url):
        return True, ""
    if online_access_enabled:
        return True, ""
    return False, "Blender online access is disabled in Preferences. Enable it or use a local provider (localhost)."


@dataclass
class Config:
    """Strongly-typed configuration container."""

    version: int = CURRENT_CONFIG_VERSION
    base_url: str = DEFAULT_BASE_URL
    api_key: str = ""
    model: str = DEFAULT_MODEL
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS

    def to_dict(self, mask_key: bool = False) -> Dict[str, Any]:
        """Convert config to dictionary, optionally masking the API key."""
        d = asdict(self)
        if mask_key:
            d["api_key"] = mask_api_key(self.api_key)
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Config":
        """Construct a Config from a dictionary with type-coercion and safety checks."""
        version = int(data.get("version", CURRENT_CONFIG_VERSION))
        base_url = str(data.get("base_url", DEFAULT_BASE_URL)).strip()
        if not base_url:
            base_url = DEFAULT_BASE_URL

        api_key = str(data.get("api_key", "")).strip()

        model = str(data.get("model", DEFAULT_MODEL)).strip()
        if not model:
            model = DEFAULT_MODEL

        try:
            timeout_seconds = float(data.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS))
            if timeout_seconds <= 0:
                timeout_seconds = DEFAULT_TIMEOUT_SECONDS
        except (ValueError, TypeError):
            timeout_seconds = DEFAULT_TIMEOUT_SECONDS

        return cls(
            version=version,
            base_url=base_url,
            api_key=api_key,
            model=model,
            timeout_seconds=timeout_seconds,
        )


def load_config(config_path: Optional[Path] = None) -> Tuple[Config, Optional[str]]:
    """Load configuration respecting priority: ENV > File > Defaults.

    Returns:
        Tuple of (Config, warning_or_error_message)
    """
    file_data: Dict[str, Any] = {}
    warning: Optional[str] = None

    if config_path and config_path.is_file():
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                content = f.read().strip()
                if content:
                    file_data = json.loads(content)
        except Exception as exc:
            warning = f"Failed to load config file at {config_path}: {exc}. Using defaults."
            file_data = {}

    config = Config.from_dict(file_data)

    # Environment variables override file/defaults
    env_url = os.environ.get("BLENDER_AI_BASE_URL") or os.environ.get("OPENAI_BASE_URL")
    if env_url:
        config.base_url = env_url.strip()

    env_key = os.environ.get("BLENDER_AI_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if env_key:
        config.api_key = env_key.strip()

    env_model = os.environ.get("BLENDER_AI_MODEL")
    if env_model:
        config.model = env_model.strip()

    env_timeout = os.environ.get("BLENDER_AI_TIMEOUT")
    if env_timeout:
        try:
            parsed_timeout = float(env_timeout)
            if parsed_timeout > 0:
                config.timeout_seconds = parsed_timeout
        except ValueError:
            pass

    return config, warning


def save_config(config: Config, config_path: Path) -> None:
    """Save configuration to disk safely with restrictive permissions."""
    config_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = config_path.with_suffix(".tmp")
    data = config.to_dict(mask_key=False)

    with open(temp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

    # Set user-only read/write on platforms that support it
    try:
        os.chmod(temp_path, 0o600)
    except Exception:
        pass

    temp_path.replace(config_path)

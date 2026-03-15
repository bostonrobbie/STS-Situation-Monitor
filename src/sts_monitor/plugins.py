"""Plugin system for dynamically loading connectors and extensions.

Plugins are discovered via:
1. Python entry points (group: "sts_monitor.connectors")
2. Python files in the ~/.sts-monitor/plugins/ directory
3. Explicit registration via register_connector()

Each plugin must expose a class implementing the Connector protocol.
"""
from __future__ import annotations

import importlib
import importlib.metadata
import importlib.util
import logging
import os
from pathlib import Path
from typing import Any, Type

from sts_monitor.connectors.base import Connector

logger = logging.getLogger(__name__)

# Plugin directory — user-local
PLUGIN_DIR = Path(os.getenv("STS_PLUGIN_DIR", str(Path.home() / ".sts-monitor" / "plugins")))


class PluginRegistry:
    """Registry for dynamically loaded connector plugins."""

    def __init__(self) -> None:
        self._connectors: dict[str, Type] = {}
        self._metadata: dict[str, dict[str, Any]] = {}

    @property
    def registered(self) -> dict[str, dict[str, Any]]:
        """Return metadata for all registered plugins."""
        return dict(self._metadata)

    def register_connector(
        self,
        name: str,
        cls: Type,
        *,
        description: str = "",
        author: str = "",
        version: str = "0.0.0",
    ) -> None:
        """Register a connector class by name."""
        self._connectors[name] = cls
        self._metadata[name] = {
            "name": name,
            "class": f"{cls.__module__}.{cls.__qualname__}",
            "description": description,
            "author": author,
            "version": version,
        }
        logger.info("Registered plugin connector: %s (%s)", name, cls.__qualname__)

    def get_connector(self, name: str) -> Type | None:
        """Get a registered connector class by name."""
        return self._connectors.get(name)

    def create_connector(self, name: str, **kwargs: Any) -> Any:
        """Instantiate a registered connector by name."""
        cls = self._connectors.get(name)
        if cls is None:
            raise KeyError(f"No connector registered with name '{name}'")
        return cls(**kwargs)

    def discover_entrypoints(self) -> int:
        """Load connectors from Python entry points (sts_monitor.connectors group)."""
        loaded = 0
        try:
            eps = importlib.metadata.entry_points()
            if hasattr(eps, "select"):
                group = eps.select(group="sts_monitor.connectors")
            else:
                group = eps.get("sts_monitor.connectors", [])
            for ep in group:
                try:
                    cls = ep.load()
                    name = ep.name
                    self.register_connector(
                        name,
                        cls,
                        description=f"Entry point plugin: {ep.value}",
                    )
                    loaded += 1
                except Exception as exc:
                    logger.warning("Failed to load entry point plugin %s: %s", ep.name, exc)
        except Exception as exc:
            logger.debug("Entry point discovery error: %s", exc)
        return loaded

    def discover_plugin_dir(self, plugin_dir: Path | None = None) -> int:
        """Load connectors from .py files in the plugin directory."""
        directory = plugin_dir or PLUGIN_DIR
        if not directory.exists():
            return 0

        loaded = 0
        for path in sorted(directory.glob("*.py")):
            if path.name.startswith("_"):
                continue
            try:
                spec = importlib.util.spec_from_file_location(
                    f"sts_plugins.{path.stem}", str(path)
                )
                if spec is None or spec.loader is None:
                    continue
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)

                # Look for classes that have a 'name' attribute and 'collect' method
                for attr_name in dir(module):
                    obj = getattr(module, attr_name)
                    if (
                        isinstance(obj, type)
                        and hasattr(obj, "collect")
                        and hasattr(obj, "name")
                        and obj is not Connector
                    ):
                        instance_name = getattr(obj, "name", path.stem)
                        self.register_connector(
                            instance_name,
                            obj,
                            description=f"File plugin: {path.name}",
                        )
                        loaded += 1
            except Exception as exc:
                logger.warning("Failed to load plugin file %s: %s", path.name, exc)

        return loaded

    def discover_all(self) -> dict[str, int]:
        """Run all discovery mechanisms. Returns counts per source."""
        return {
            "entrypoints": self.discover_entrypoints(),
            "plugin_dir": self.discover_plugin_dir(),
        }


# Global singleton
plugin_registry = PluginRegistry()

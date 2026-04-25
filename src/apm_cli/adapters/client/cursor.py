"""Cursor IDE implementation of MCP client adapter.

Cursor uses the standard ``mcpServers`` JSON format at ``.cursor/mcp.json``
(repo-local).  Cursor's schema differs from Copilot CLI in key ways:

- ``type`` must be ``"stdio"`` or ``"http"`` (not ``"local"``).
- ``tools`` and ``id`` fields are not supported.

This adapter delegates config formatting to :class:`CopilotClientAdapter`
and then transforms the result for Cursor compatibility.

APM only writes to ``.cursor/mcp.json`` when the ``.cursor/`` directory
already exists -- Cursor support is opt-in.
"""

import json
import os
from pathlib import Path
from typing import Optional

from .copilot import CopilotClientAdapter


class CursorClientAdapter(CopilotClientAdapter):
    """Cursor IDE MCP client adapter.

    Inherits config path and read/write logic from :class:`CopilotClientAdapter`
    and uses the delegate-then-transform pattern for ``_format_server_config``:

    - Calls ``super()._format_server_config()`` to get the fully-resolved config
      (GitHub auth, header env-var resolution, ``_warn_input_variables``, etc.)
    - Translates Cursor-incompatible fields:
      - ``type: "local"`` becomes ``"stdio"`` (Cursor schema)
      - ``sse/streamable-http`` transports normalized to ``"http"``
      - Copilot-specific ``tools`` and ``id`` fields stripped
    """

    supports_user_scope: bool = False

    # ------------------------------------------------------------------ #
    # Config path
    # ------------------------------------------------------------------ #

    def get_config_path(self):
        """Return the path to ``.cursor/mcp.json`` in the repository root.

        Unlike the Copilot adapter this is a **repo-local** path.  The
        ``.cursor/`` directory is *not* created automatically -- APM only
        writes here when the directory already exists.
        """
        cursor_dir = Path(os.getcwd()) / ".cursor"
        return str(cursor_dir / "mcp.json")

    # ------------------------------------------------------------------ #
    # Config read / write — override to avoid auto-creating the directory
    # ------------------------------------------------------------------ #

    def update_config(self, config_updates):
        """Merge *config_updates* into the ``mcpServers`` section.

        The ``.cursor/`` directory must already exist; if it does not, this
        method returns silently (opt-in behaviour).
        """
        config_path = Path(self.get_config_path())

        # Opt-in: only write when .cursor/ already exists
        if not config_path.parent.exists():
            return

        current_config = self.get_current_config()
        if "mcpServers" not in current_config:
            current_config["mcpServers"] = {}

        current_config["mcpServers"].update(config_updates)

        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(current_config, f, indent=2)

    def get_current_config(self):
        """Read the current ``.cursor/mcp.json`` contents."""
        config_path = self.get_config_path()

        if not os.path.exists(config_path):
            return {}

        try:
            with open(config_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return {}

    # ------------------------------------------------------------------ #
    # configure_mcp_server — thin override for the print label
    # ------------------------------------------------------------------ #

    def configure_mcp_server(
        self,
        server_url,
        server_name=None,
        enabled=True,
        env_overrides=None,
        server_info_cache=None,
        runtime_vars=None,
    ):
        """Configure an MCP server in Cursor's ``.cursor/mcp.json``.

        Delegates entirely to the parent implementation but prints a
        Cursor-specific success message.
        """
        if not server_url:
            print("Error: server_url cannot be empty")
            return False

        # Opt-in: skip silently when .cursor/ does not exist
        cursor_dir = Path(os.getcwd()) / ".cursor"
        if not cursor_dir.exists():
            return True  # nothing to do, not an error

        try:
            # Use cached server info if available, otherwise fetch from registry
            if server_info_cache and server_url in server_info_cache:
                server_info = server_info_cache[server_url]
            else:
                server_info = self.registry_client.find_server_by_reference(server_url)

            if not server_info:
                print(f"Error: MCP server '{server_url}' not found in registry")
                return False

            # Determine config key
            if server_name:
                config_key = server_name
            elif "/" in server_url:
                config_key = server_url.split("/")[-1]
            else:
                config_key = server_url

            server_config = self._format_server_config(
                server_info, env_overrides, runtime_vars
            )
            self.update_config({config_key: server_config})

            print(
                f"Successfully configured MCP server '{config_key}' for Cursor"
            )
            return True

        except Exception as e:
            print(f"Error configuring MCP server: {e}")
            return False

    # ------------------------------------------------------------------ #
    # _format_server_config — delegate to parent, then transform for Cursor
    # ------------------------------------------------------------------ #

    def _format_server_config(
        self,
        server_info: dict,
        env_overrides: Optional[dict] = None,
        runtime_vars: Optional[dict] = None,
    ) -> dict:
        """Format server config via parent, then adapt for Cursor schema.

        Cursor requires ``type: "stdio"`` (not ``"local"``) and does not
        support the Copilot-specific ``tools`` and ``id`` fields.
        """
        config = super()._format_server_config(server_info, env_overrides, runtime_vars)

        # Adapt type field for Cursor compatibility
        if config.get("type") == "local":
            config["type"] = "stdio"
        elif config.get("type") == "http":
            # For remote endpoints, ensure type remains http (as opposed to sse, etc.)
            # and normalize sse/streamable-http to http for Cursor
            transport_type = server_info.get("remotes", [{}])[0].get("transport_type", "")
            if transport_type in ("sse", "streamable-http"):
                config["type"] = "http"

        # Remove Copilot-only fields that cause Cursor's MCP loader to silently reject servers
        config.pop("tools", None)
        config.pop("id", None)

        return config

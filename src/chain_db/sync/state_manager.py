"""State Manager: tracks sync progress for the table sync module.

Persists the synced block height and table versions to a local file,
enabling resumption from the last checkpoint after a restart.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from loguru import logger


class StateManager:
    """Manages the synchronization state of the local node.

    Tracks:
    - synced_height: The last block number that has been synced
    - table_versions: Version numbers for each synced table

    The state is persisted to a JSON file for crash recovery.
    """

    def __init__(self, data_dir: str = "./data") -> None:
        """Initialize the state manager.

        Args:
            data_dir: Directory for the state file.
        """
        self.data_dir = data_dir
        self.synced_height: int = 0
        self.table_versions: dict[str, int] = {}
        self._state_file = Path(data_dir) / "sync_state.json"

        # Load existing state if available
        self._load()

    def update(self, height: int, table_name: str | None = None, version: int | None = None) -> None:
        """Update the sync progress.

        Args:
            height: Block number that has been synced.
            table_name: Optional table name to update version.
            version: Optional version number for the table.
        """
        if height > self.synced_height:
            self.synced_height = height

        if table_name and version is not None:
            self.table_versions[table_name] = version

    def get_sync_status(self) -> dict[str, Any]:
        """Get the current sync status.

        Returns:
            Dictionary with synced_height and table_versions.
        """
        return {
            "synced_height": self.synced_height,
            "table_versions": self.table_versions,
        }

    async def save(self) -> None:
        """Persist the current state to the state file."""
        state = {
            "synced_height": self.synced_height,
            "table_versions": self.table_versions,
        }

        # Ensure directory exists
        self._state_file.parent.mkdir(parents=True, exist_ok=True)

        with open(self._state_file, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)

        logger.debug("State saved: synced_height={}", self.synced_height)

    def _load(self) -> None:
        """Load state from the state file."""
        if self._state_file.exists():
            try:
                with open(self._state_file, encoding="utf-8") as f:
                    state = json.load(f)
                self.synced_height = state.get("synced_height", 0)
                self.table_versions = state.get("table_versions", {})
                logger.info("State loaded: synced_height={}", self.synced_height)
            except Exception as e:
                logger.warning("Failed to load state: {}", e)
                self.synced_height = 0
                self.table_versions = {}
        else:
            self.synced_height = 0
            self.table_versions = {}

    def reset(self) -> None:
        """Reset the sync state to initial values."""
        self.synced_height = 0
        self.table_versions = {}
        logger.info("State reset")

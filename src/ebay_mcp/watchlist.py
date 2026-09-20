"""Server-side watchlist.

eBay's public Buy APIs do not expose watchlist management, so the connector
keeps its own watchlist in a JSON file and re-checks each entry via
``getItem`` when asked. Entries are keyed by RESTful item id.
"""
from __future__ import annotations

import json
import time
from pathlib import Path


class WatchlistStore:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self._entries: dict[str, dict] = {}
        self.load()

    def load(self) -> None:
        try:
            data = json.loads(self.path.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            data = {}
        self._entries = data if isinstance(data, dict) else {}

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._entries, indent=2))
        tmp.replace(self.path)

    def add(
        self,
        item_id: str,
        title: str,
        target_price: str | None = None,
        currency: str = "USD",
        last_price: str | None = None,
    ) -> dict:
        entry = {
            "item_id": item_id,
            "title": title,
            "target_price": target_price,
            "currency": currency,
            "added_at": int(time.time()),
            "last_price": last_price,
            "last_status": "watching",
        }
        self._entries[item_id] = entry
        self.save()
        return entry

    def remove(self, item_id: str) -> bool:
        if item_id in self._entries:
            del self._entries[item_id]
            self.save()
            return True
        return False

    def all(self) -> list[dict]:
        return list(self._entries.values())

    def update(self, item_id: str, **fields) -> None:
        if item_id in self._entries:
            self._entries[item_id].update(fields)
            self.save()

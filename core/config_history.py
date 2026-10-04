import json
import time
import os
import hashlib
import logging

from core.services import metrics, load_json, save_json, DATA_DIR, CONFIG_PATH

logger = logging.getLogger("turtle.config_history")

HISTORY_PATH = os.path.join(DATA_DIR, "config_history.json")

SENSITIVE_KEYS = ("token", "secret", "key", "password", "api_key", "auth")
MAX_VERSIONS = 50


def _is_sensitive(key: str) -> bool:
    low = key.lower()
    return any(s in low for s in SENSITIVE_KEYS)


def _hash_config(cfg: dict) -> str:
    raw = json.dumps(cfg, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _redact(obj, parent_key=""):
    if isinstance(obj, dict):
        return {
            k: "***" if _is_sensitive(k) else _redact(v, k)
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [_redact(item, parent_key) for item in obj]
    return obj


class ConfigHistory:
    def __init__(self):
        self._history: list[dict] = load_json(HISTORY_PATH, [])
        logger.info("ConfigHistory loaded %d versions", len(self._history))

    # ------------------------------------------------------------------
    # Snapshot
    # ------------------------------------------------------------------
    def snapshot(self, admin_id: str, reason: str = "manual") -> dict:
        if not os.path.isfile(CONFIG_PATH):
            logger.warning("config.json not found at %s - cannot snapshot", CONFIG_PATH)
            return {}

        with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
            cfg = json.load(fh)

        cfg_hash = _hash_config(cfg)

        if self._history and self._history[-1].get("hash") == cfg_hash:
            logger.debug("Config unchanged since last snapshot - skipping")
            return self._history[-1]

        version_num = (self._history[-1]["version_num"] + 1) if self._history else 1

        entry = {
            "version_num": version_num,
            "timestamp": time.time(),
            "admin_id": admin_id,
            "reason": reason,
            "hash": cfg_hash,
            "config_snapshot": cfg,
        }

        self._history.append(entry)

        if len(self._history) > MAX_VERSIONS:
            self._history = self._history[-MAX_VERSIONS:]

        self._save()
        metrics.inc("config_history.snapshot", tags={"reason": reason})
        logger.info("Snapshot v%d created by %s (%s)", version_num, admin_id, reason)
        return entry

    # ------------------------------------------------------------------
    # Get versions (safe for display)
    # ------------------------------------------------------------------
    def get_versions(self) -> list[dict]:
        return [
            {
                "version_num": v["version_num"],
                "timestamp": v["timestamp"],
                "admin_id": v["admin_id"],
                "reason": v["reason"],
            }
            for v in self._history
        ]

    # ------------------------------------------------------------------
    # Get single version
    # ------------------------------------------------------------------
    def get_version(self, version_num: int) -> dict | None:
        for v in self._history:
            if v["version_num"] == version_num:
                return {
                    "version_num": v["version_num"],
                    "timestamp": v["timestamp"],
                    "admin_id": v["admin_id"],
                    "reason": v["reason"],
                    "hash": v["hash"],
                    "config_snapshot": _redact(v["config_snapshot"]),
                }
        return None

    # ------------------------------------------------------------------
    # Compare
    # ------------------------------------------------------------------
    def compare(self, v1: int, v2: int) -> list[dict]:
        cfg_a = None
        cfg_b = None
        for v in self._history:
            if v["version_num"] == v1:
                cfg_a = v["config_snapshot"]
            if v["version_num"] == v2:
                cfg_b = v["config_snapshot"]

        if cfg_a is None:
            logger.warning("Version %d not found", v1)
            return []
        if cfg_b is None:
            logger.warning("Version %d not found", v2)
            return []

        diffs = self._diff_dicts(cfg_a, cfg_b)
        for d in diffs:
            if _is_sensitive(d["key"]):
                d["old_value"] = "***"
                d["new_value"] = "***"
        return diffs

    def _diff_dicts(self, a: dict, b: dict, prefix: str = "") -> list[dict]:
        results = []
        all_keys = set(list(a.keys()) + list(b.keys()))
        for key in sorted(all_keys):
            full_key = f"{prefix}.{key}" if prefix else key
            val_a = a.get(key, "<missing>")
            val_b = b.get(key, "<missing>")

            if isinstance(val_a, dict) and isinstance(val_b, dict):
                results.extend(self._diff_dicts(val_a, val_b, full_key))
            elif val_a != val_b:
                results.append({"key": full_key, "old_value": val_a, "new_value": val_b})
        return results

    # ------------------------------------------------------------------
    # Restore
    # ------------------------------------------------------------------
    def restore(self, version_num: int, admin_id: str) -> dict:
        target = None
        for v in self._history:
            if v["version_num"] == version_num:
                target = v
                break

        if target is None:
            return {"success": False, "restored_version": None, "error": "Version not found"}

        cfg = target["config_snapshot"]
        try:
            with open(CONFIG_PATH, "w", encoding="utf-8") as fh:
                json.dump(cfg, fh, indent=2, ensure_ascii=False)
        except OSError as exc:
            logger.error("Failed to write config.json: %s", exc)
            return {"success": False, "restored_version": None, "error": str(exc)}

        entry = self.snapshot(admin_id, reason=f"restored from v{version_num}")
        metrics.inc("config_history.restore", tags={"from": str(version_num)})
        logger.info("Config restored to v%d by %s", version_num, admin_id)
        return {"success": True, "restored_version": entry.get("version_num")}

    # ------------------------------------------------------------------
    # Auto-snapshot
    # ------------------------------------------------------------------
    def auto_snapshot(self):
        if not os.path.isfile(CONFIG_PATH):
            return
        with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
            cfg = json.load(fh)

        cfg_hash = _hash_config(cfg)
        if self._history and self._history[-1].get("hash") == cfg_hash:
            return

        self.snapshot("system", reason="auto")

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------
    def _save(self):
        save_json(HISTORY_PATH, self._history)

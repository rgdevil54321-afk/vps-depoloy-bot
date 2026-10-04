import copy
import json
import os
from typing import Any, Dict, List, Optional, Tuple

from core.services import load_json, save_json, CONFIG_PATH, DATA_DIR


DEFAULTS: Dict[str, Any] = {
    "MAIN_ADMIN_ID":         0,
    "VPS_USER_ROLE_ID":      0,
    "DOCKER_IMAGE":          "ubuntu:22.04",
    "LOG_CHANNEL_ID":        None,
    "CPU_THRESHOLD":         80,
    "CHECK_INTERVAL":        60,
    "SSH_PORT_START":        22000,
    "MAINTENANCE_MODE":      False,
    "PURCHASES_DISABLED":    False,
    "BACKUP_MAX_COUNT":      5,
    "BACKUP_MAX_AGE_DAYS":   30,
    "AUTO_BACKUP_ENABLED":   True,
    "AUTO_BACKUP_INTERVAL":  3600,
    "ALERT_CHANNEL_ID":      None,
    "MONITORING_ENABLED":    True,
    "RATE_LIMIT_PER_MIN":    30,
    "LOG_LEVEL":             "INFO",
}

CATEGORIES: Dict[str, List[str]] = {
    "Discord":     ["MAIN_ADMIN_ID", "VPS_USER_ROLE_ID", "LOG_CHANNEL_ID", "ALERT_CHANNEL_ID"],
    "Docker":      ["DOCKER_IMAGE"],
    "VPS":         ["SSH_PORT_START", "CPU_THRESHOLD", "CHECK_INTERVAL"],
    "Billing":     ["PURCHASES_DISABLED"],
    "Maintenance": ["MAINTENANCE_MODE"],
    "Backups":     ["BACKUP_MAX_COUNT", "BACKUP_MAX_AGE_DAYS", "AUTO_BACKUP_ENABLED", "AUTO_BACKUP_INTERVAL"],
    "Monitoring":  ["MONITORING_ENABLED"],
    "Security":    ["RATE_LIMIT_PER_MIN"],
    "Logging":     ["LOG_LEVEL"],
}


class ConfigManager:
    def __init__(self) -> None:
        self._path = CONFIG_PATH
        self._data: Dict[str, Any] = {}
        self._load()

    # ------------------------------------------------------------------
    # internal
    # ------------------------------------------------------------------
    def _load(self) -> None:
        raw = load_json(self._path, {})
        self._data = copy.deepcopy(DEFAULTS)
        for key in DEFAULTS:
            if key in raw:
                self._data[key] = raw[key]

    def _persist(self) -> None:
        save_json(self._path, self._data)

    # ------------------------------------------------------------------
    # getters / setters
    # ------------------------------------------------------------------
    def get_all(self) -> Dict[str, Any]:
        return copy.deepcopy(self._data)

    def get(self, key: str) -> Any:
        if key not in DEFAULTS:
            raise KeyError(f"Unknown config key: {key}")
        return self._data.get(key, DEFAULTS[key])

    def set(self, key: str, value: Any, validate: bool = True) -> dict:
        if key not in DEFAULTS:
            return {"success": False, "old_value": None, "new_value": None, "error": f"Unknown key: {key}"}

        if validate:
            ok, err = self.validate(key, value)
            if not ok:
                return {"success": False, "old_value": self._data[key], "new_value": value, "error": err}

        old = self._data[key]
        self._data[key] = value
        self._persist()
        return {"success": True, "old_value": old, "new_value": value, "error": None}

    # ------------------------------------------------------------------
    # validation
    # ------------------------------------------------------------------
    def validate(self, key: str, value: Any) -> Tuple[bool, Optional[str]]:
        if key not in DEFAULTS:
            return False, f"Unknown key: {key}"

        expected_type = type(DEFAULTS[key])

        if DEFAULTS[key] is None:
            if value is not None and not isinstance(value, (int, str, bool)):
                return False, f"Key '{key}' expects int, str, bool or None"
            return True, None

        if isinstance(DEFAULTS[key], bool):
            if isinstance(value, bool):
                return True, None
            if isinstance(value, int) and value in (0, 1):
                return True, None
            return False, f"Key '{key}' expects bool, got {type(value).__name__}"

        if isinstance(DEFAULTS[key], int):
            if isinstance(value, bool):
                return False, f"Key '{key}' expects int, got bool"
            if isinstance(value, int):
                return True, None
            return False, f"Key '{key}' expects int, got {type(value).__name__}"

        if isinstance(DEFAULTS[key], str):
            if isinstance(value, str):
                allowed = {
                    "LOG_LEVEL": {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"},
                }
                if key in allowed and value not in allowed[key]:
                    return False, f"Key '{key}' must be one of {allowed[key]}"
                return True, None
            return False, f"Key '{key}' expects str, got {type(value).__name__}"

        return True, None

    # ------------------------------------------------------------------
    # categories
    # ------------------------------------------------------------------
    def get_categories(self) -> Dict[str, List[str]]:
        return copy.deepcopy(CATEGORIES)

    def get_category(self, name: str) -> Dict[str, Any]:
        keys = CATEGORIES.get(name)
        if keys is None:
            raise KeyError(f"Unknown category: {name}")
        return {k: self._data[k] for k in keys}

    # ------------------------------------------------------------------
    # reset
    # ------------------------------------------------------------------
    def reset_to_default(self, key: str) -> dict:
        if key not in DEFAULTS:
            return {"success": False, "error": f"Unknown key: {key}"}
        old = self._data[key]
        self._data[key] = copy.deepcopy(DEFAULTS[key])
        self._persist()
        return {"success": True, "old_value": old, "new_value": self._data[key], "error": None}

    def reset_all(self) -> dict:
        old = copy.deepcopy(self._data)
        self._data = copy.deepcopy(DEFAULTS)
        self._persist()
        return {"success": True, "old_value": old, "new_value": copy.deepcopy(self._data), "error": None}

    # ------------------------------------------------------------------
    # import / export
    # ------------------------------------------------------------------
    def export_config(self, path: str) -> dict:
        try:
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(self._data, fh, indent=2, default=str)
            return {"success": True, "error": None}
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    def import_config(self, path: str) -> dict:
        try:
            with open(path, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
        except Exception as exc:
            return {"success": False, "error": f"Failed to read file: {exc}"}

        errors: List[str] = []
        for key in DEFAULTS:
            if key in raw:
                ok, err = self.validate(key, raw[key])
                if not ok:
                    errors.append(err)
                    continue
                self._data[key] = raw[key]

        if errors:
            self._persist()
            return {"success": False, "error": "Validation errors: " + "; ".join(errors)}

        self._persist()
        return {"success": True, "error": None}

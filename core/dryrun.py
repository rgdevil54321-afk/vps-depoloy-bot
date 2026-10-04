import json
import time
import os
import logging
import uuid

from core.services import metrics, bus, load_json, save_json, DATA_DIR

logger = logging.getLogger("turtle.dryrun")

RISK_MAP = {
    "purge": "high",
    "reinstall": "high",
    "restore": "high",
    "config_change": "medium",
    "resource_change": "medium",
    "node_change": "medium",
    "service_change": "low",
}

DURATION_ESTIMATE = {
    "purge": "5-15 seconds",
    "reinstall": "30-120 seconds",
    "restore": "10-30 seconds",
    "config_change": "1-3 seconds",
    "resource_change": "5-10 seconds",
    "node_change": "5-20 seconds",
    "service_change": "2-10 seconds",
}


class DryRunManager:
    def __init__(self):
        self._log_path = os.path.join(DATA_DIR, "dryrun_log.json")
        self._active_ops = {}
        logger.info("DryRunManager initialised")

    # ------------------------------------------------------------------
    # Preview
    # ------------------------------------------------------------------
    def preview(self, action_type: str, params: dict) -> dict:
        if action_type not in RISK_MAP:
            return {
                "action": action_type,
                "params": params,
                "will_affect": [],
                "risk_level": "low",
                "warnings": [f"Unknown action type '{action_type}'"],
                "estimated_duration": "unknown",
            }

        builder = getattr(self, f"_preview_{action_type}", None)
        if builder:
            result = builder(params)
        else:
            result = {"will_affect": [], "warnings": []}

        result["action"] = action_type
        result["params"] = params
        result["risk_level"] = RISK_MAP.get(action_type, "low")
        result["estimated_duration"] = DURATION_ESTIMATE.get(action_type, "unknown")
        return result

    # ---- per-type preview helpers ------------------------------------
    def _preview_purge(self, params: dict) -> dict:
        target = params.get("target", "unknown")
        files = params.get("files", [])
        will_affect = [f"Server files for '{target}'"]
        warnings = []
        if not files:
            warnings.append("No specific files listed - entire directory may be removed")
        backups = load_json(os.path.join(DATA_DIR, "backups.json"), [])
        server_backups = [b for b in backups if b.get("server") == target]
        if not server_backups:
            warnings.append("No backups exist for this server - data will be unrecoverable")
        return {"will_affect": will_affect, "warnings": warnings}

    def _preview_reinstall(self, params: dict) -> dict:
        target = params.get("target", "unknown")
        image = params.get("image", "default")
        will_affect = [
            f"Server '{target}' runtime environment",
            f"Docker container for '{target}'",
        ]
        warnings = [
            "All running processes will be stopped",
            f"Container image will change to {image}",
        ]
        return {"will_affect": will_affect, "warnings": warnings}

    def _preview_restore(self, params: dict) -> dict:
        backup_id = params.get("backup_id", "unknown")
        target = params.get("target", "unknown")
        will_affect = [f"Server '{target}' - full state overwrite"]
        warnings = [
            "Current state will be replaced by the backup",
            f"Restoring backup '{backup_id}'",
        ]
        return {"will_affect": will_affect, "warnings": warnings}

    def _preview_config_change(self, params: dict) -> dict:
        changes = params.get("changes", {})
        will_affect = [f"Config key '{k}'" for k in changes]
        warnings = []
        for k in changes:
            if "token" in k.lower() or "secret" in k.lower():
                warnings.append(f"Key '{k}' contains sensitive data")
        return {"will_affect": will_affect, "warnings": warnings}

    def _preview_resource_change(self, params: dict) -> dict:
        target = params.get("target", "unknown")
        will_affect = [f"Resource allocation for '{target}'"]
        warnings = []
        if params.get("cpu") and float(params.get("cpu", 0)) > 4:
            warnings.append("CPU allocation exceeds 4 cores - may impact host")
        if params.get("memory") and "g" in str(params.get("memory", "0")).lower():
            val = float(str(params.get("memory", "0")).replace("g", ""))
            if val > 8:
                warnings.append("Memory allocation exceeds 8 GB - may impact host")
        return {"will_affect": will_affect, "warnings": warnings}

    def _preview_node_change(self, params: dict) -> dict:
        node_id = params.get("node_id", "unknown")
        will_affect = [f"Node '{node_id}' configuration"]
        warnings = []
        if params.get("action") == "decommission":
            warnings.append("Decommissioning will remove all servers on this node")
        return {"will_affect": will_affect, "warnings": warnings}

    def _preview_service_change(self, params: dict) -> dict:
        service = params.get("service", "unknown")
        action = params.get("action", "unknown")
        will_affect = [f"Service '{service}' - {action}"]
        warnings = []
        if action == "stop":
            warnings.append("Dependent services may be affected")
        return {"will_affect": will_affect, "warnings": warnings}

    # ------------------------------------------------------------------
    # Validate
    # ------------------------------------------------------------------
    def validate(self, action_type: str, params: dict) -> dict:
        errors = []
        warnings = []

        if action_type not in RISK_MAP:
            errors.append(f"Unknown action type: {action_type}")
            return {"valid": False, "errors": errors, "warnings": warnings}

        if action_type == "purge":
            target = params.get("target")
            if not target:
                errors.append("Purge requires a 'target' server name")
            backups = load_json(os.path.join(DATA_DIR, "backups.json"), [])
            server_backups = [b for b in backups if b.get("server") == target]
            if not server_backups:
                warnings.append("No backups exist for this server")

        elif action_type == "reinstall":
            target = params.get("target")
            if not target:
                errors.append("Reinstall requires a 'target' server name")

        elif action_type == "restore":
            if not params.get("backup_id"):
                errors.append("Restore requires a 'backup_id'")
            if not params.get("target"):
                errors.append("Restore requires a 'target' server name")

        elif action_type == "config_change":
            changes = params.get("changes", {})
            if not changes:
                errors.append("config_change requires a non-empty 'changes' dict")

        elif action_type == "resource_change":
            if not params.get("target"):
                errors.append("resource_change requires a 'target'")

        elif action_type == "node_change":
            if not params.get("node_id"):
                errors.append("node_change requires a 'node_id'")

        elif action_type == "service_change":
            if not params.get("service"):
                errors.append("service_change requires a 'service' name")
            if params.get("action") not in ("start", "stop", "restart"):
                errors.append("service_change action must be start, stop, or restart")

        return {"valid": len(errors) == 0, "errors": errors, "warnings": warnings}

    # ------------------------------------------------------------------
    # Diff
    # ------------------------------------------------------------------
    def get_diff(self, action_type: str, old_params: dict, new_params: dict) -> str:
        if action_type != "config_change":
            return f"Diff not supported for action type '{action_type}'"

        old_changes = old_params.get("changes", {})
        new_changes = new_params.get("changes", {})
        all_keys = sorted(set(list(old_changes.keys()) + list(new_changes.keys())))

        lines = ["--- Config Diff ---"]
        for key in all_keys:
            old_val = old_changes.get(key, "<not set>")
            new_val = new_changes.get(key, "<not set>")
            if any(s in key.lower() for s in ("token", "secret", "key", "password")):
                old_val = "***"
                new_val = "***"
            lines.append(f"  {key}: {old_val} -> {new_val}")

        if not all_keys:
            lines.append("  (no differences)")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Confirm & Execute
    # ------------------------------------------------------------------
    def confirm_and_execute(self, action_type: str, params: dict, admin_id: str) -> dict:
        operation_id = str(uuid.uuid4())[:12]
        validation = self.validate(action_type, params)
        if not validation["valid"]:
            return {"proceed": False, "operation_id": None, "errors": validation["errors"]}

        preview = self.preview(action_type, params)

        entry = {
            "operation_id": operation_id,
            "action_type": action_type,
            "params": params,
            "admin_id": admin_id,
            "timestamp": time.time(),
            "risk_level": preview["risk_level"],
            "warnings": preview["warnings"],
        }

        self._active_ops[operation_id] = entry
        self._append_log(entry)
        metrics.inc("dryrun.created", tags={"action": action_type, "risk": preview["risk_level"]})
        bus.emit("dryrun.confirmed", entry)
        logger.info("Dry run confirmed: %s by admin %s", operation_id, admin_id)

        return {"proceed": True, "operation_id": operation_id, "preview": preview}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _append_log(self, entry: dict):
        log = load_json(self._log_path, [])
        log.append(entry)
        if len(log) > 500:
            log = log[-500:]
        save_json(self._log_path, log)

    def get_operation(self, operation_id: str) -> dict | None:
        return self._active_ops.get(operation_id)

    def clear_operation(self, operation_id: str):
        self._active_ops.pop(operation_id, None)

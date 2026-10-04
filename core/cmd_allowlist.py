"""Command Allowlisting system.

Only allows predefined and approved infrastructure operations. Prevents arbitrary
shell command execution. Validates all parameters. Rejects unknown/dangerous ops.
Keeps infrastructure actions separated from unrestricted terminal access.
"""
import os
import re
import shlex
import logging
import threading

from core.services import metrics, bus

logger = logging.getLogger("turtle.cmd_allowlist")

ALLOWED_DOCKER_COMMANDS = {
    "ps", "inspect", "logs", "stats", "top", "port", "diff", "commit",
    "cp", "exec", "run", "start", "stop", "restart", "pause", "unpause",
    "kill", "rm", "rmi", "pull", "build", "images", "volume",
    "network", "system", "info", "version", "tag", "save", "load",
}

BLOCKED_DOCKER_FLAGS = {
    "--privileged", "--pid=host", "--net=host", "--ipc=host",
    "--cap-add=SYS_ADMIN", "--cap-add=SYS_PTRACE",
}

ALLOWED_SYSTEM_COMMANDS = {
    "docker", "systemctl", "journalctl", "df", "free", "uptime",
    "uname", "top", "htop", "ps", "ls", "cat", "head", "tail",
    "grep", "find", "wc", "du", "stat", "date", "whoami", "id",
    "ip", "ss", "netstat", "dig", "ping", "curl", "wget",
}

BLOCKED_COMMANDS = {
    "rm -rf /", "rm -rf /*", "dd if=", "mkfs", ":(){:|:&};:",
    "chmod -R 777 /", "wget", "curl", "> /dev/sda",
}

SHELL_INJECTION_PATTERNS = [
    re.compile(r'[;&|`]'),
    re.compile(r'\$\('),
    re.compile(r'\$\{'),
    re.compile(r'\n\s*(rm|dd|mkfs|chmod|chown)'),
]


class CommandAllowlist:
    def __init__(self):
        self._lock = threading.Lock()
        self._custom_allowed: set = set()
        self._custom_blocked: set = set()
        self._execution_log: list = []
        logger.info("CommandAllowlist initialized")

    def validate_docker_command(self, args_list):
        if not args_list:
            return False, "Empty command"
        cmd = args_list[0]
        if cmd not in ALLOWED_DOCKER_COMMANDS:
            return False, f"Docker command '{cmd}' is not in allowlist"
        for arg in args_list[1:]:
            if arg in BLOCKED_DOCKER_FLAGS:
                return False, f"Blocked flag: {arg}"
            if arg.startswith("--privileged"):
                return False, "Privileged mode not allowed"
            if "--pid=host" in arg:
                return False, "--pid=host not allowed"
            if "--net=host" in arg and cmd != "run":
                return False, "--net=host not allowed for this command"
        return True, "ok"

    def validate_system_command(self, command_str):
        for blocked in BLOCKED_COMMANDS:
            if blocked in command_str.lower():
                return False, f"Blocked pattern: {blocked}"
        for pattern in SHELL_INJECTION_PATTERNS:
            if pattern.search(command_str):
                return False, "Potential shell injection detected"
        try:
            parts = shlex.split(command_str)
        except ValueError:
            return False, "Invalid command syntax"
        if not parts:
            return False, "Empty command"
        base_cmd = parts[0].split("/")[-1]
        if base_cmd in self._custom_blocked:
            return False, f"Command '{base_cmd}' is explicitly blocked"
        if base_cmd not in ALLOWED_SYSTEM_COMMANDS and base_cmd not in self._custom_allowed:
            return False, f"Command '{base_cmd}' is not in the allowlist"
        return True, "ok"

    def validate_vps_operation(self, operation, params=None):
        allowed_ops = {
            "start", "stop", "restart", "status", "create", "delete",
            "reinstall", "exec", "logs", "stats", "ports",
            "backup_create", "backup_restore", "rename",
            "protect", "unprotect",
        }
        if operation not in allowed_ops:
            return False, f"Unknown VPS operation: {operation}"
        if params:
            if isinstance(params, dict):
                for k, v in params.items():
                    if isinstance(v, str):
                        if any(c in v for c in ['`', '$', ';', '|', '&']):
                            return False, f"Suspicious parameter value in '{k}'"
        return True, "ok"

    def validate_backup_operation(self, operation):
        allowed = {"create", "restore", "delete", "list", "verify", "stats"}
        if operation not in allowed:
            return False, f"Unknown backup operation: {operation}"
        return True, "ok"

    def add_allowed(self, command):
        with self._lock:
            self._custom_allowed.add(command)
        logger.info("Added custom allowed command: %s", command)

    def add_blocked(self, command):
        with self._lock:
            self._custom_blocked.add(command)
        logger.info("Added custom blocked command: %s", command)

    def log_execution(self, command, user_id, result, allowed):
        entry = {
            "command": command[:200],
            "user_id": str(user_id),
            "result": result,
            "allowed": allowed,
        }
        with self._lock:
            self._execution_log.append(entry)
            if len(self._execution_log) > 1000:
                self._execution_log = self._execution_log[-500:]
        if not allowed:
            metrics.inc("cmd_allowlist.blocked")
            bus.emit("cmd_allowlist.blocked", command=command[:100], user_id=str(user_id))
            logger.warning("Blocked command: user=%s cmd=%s", user_id, command[:100])

    def get_execution_log(self, limit=50):
        with self._lock:
            return list(self._execution_log[-limit:])

    def get_status(self):
        with self._lock:
            return {
                "allowed_docker_commands": len(ALLOWED_DOCKER_COMMANDS),
                "allowed_system_commands": len(ALLOWED_SYSTEM_COMMANDS),
                "custom_allowed": len(self._custom_allowed),
                "custom_blocked": len(self._custom_blocked),
                "recent_blocks": sum(1 for e in self._execution_log[-100:] if not e["allowed"]),
            }


cmd_allowlist = CommandAllowlist()

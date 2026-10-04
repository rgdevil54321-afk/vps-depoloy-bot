"""Secret Protection system.

Never exposes API keys, tokens, passwords, SSH credentials, DB credentials,
env vars, or other secrets through Discord. Auto-redacts secrets from logs
and error messages. Prevents sensitive config values from appearing in output.
"""
import os
import re
import logging
import threading

from core.services import metrics, bus, load_json, DATA_DIR

logger = logging.getLogger("turtle.secrets")

SECRET_PATTERNS = [
    re.compile(r'(?i)(token|api[_-]?key|secret|password|passwd|pwd|ssh[_-]?key|private[_-]?key|auth[_-]?token|bearer)\s*[=:]\s*["\']?([^\s"\'<>]{8,})["\']?'),
    re.compile(r'(?i)(sk_live|pk_live|sk_test|pk_test)[-_]([a-zA-Z0-9]{20,})'),
    re.compile(r'(?i)ghp_[a-zA-Z0-9]{36}'),
    re.compile(r'(?i)discord[_-]?token\s*[=:]\s*["\']?([a-zA-Z0-9._-]{50,})["\']?'),
    re.compile(r'(?i)eyJ[a-zA-Z0-9_-]{20,}\.[a-zA-Z0-9_-]{20,}\.[a-zA-Z0-9_-]{20,}'),
    re.compile(r'(?i)(ssh-rsa|ssh-ed25519|ecdsa-sha2)\s+[A-Za-z0-9+/=]{40,}'),
    re.compile(r'(?i)-----BEGIN\s+(RSA\s+)?PRIVATE\s+KEY-----'),
]

REDACTED = "[REDACTED]"

REDACTED_FIELDS = {
    "token", "api_key", "secret", "password", "passwd", "ssh_key",
    "private_key", "auth_token", "totp_secret", "db_password",
    "database_password", "aws_secret", "stripe_secret", "discord_token",
    "bot_token", "webhook_secret", "encryption_key",
}


class SecretGuard:
    def __init__(self):
        self._lock = threading.Lock()
        self._redactions = 0
        self._blocked = 0
        logger.info("SecretGuard initialized")

    def redact_string(self, text):
        if not isinstance(text, str):
            return text
        original_len = len(text)
        redacted = text
        for pattern in SECRET_PATTERNS:
            redacted = pattern.sub(lambda m: m.group(0)[:min(12, len(m.group(0)))] + REDACTED, redacted)
        if redacted != text:
            with self._lock:
                self._redactions += 1
            metrics.inc("secrets.redacted")
        return redacted

    def redact_dict(self, data):
        if isinstance(data, dict):
            result = {}
            for k, v in data.items():
                if k.lower() in REDACTED_FIELDS and isinstance(v, str) and len(v) > 4:
                    result[k] = v[:2] + REDACTED
                elif isinstance(v, (dict, list)):
                    result[k] = self.redact_dict(v)
                else:
                    result[k] = self.redact_string(str(v)) if isinstance(v, str) else v
            return result
        elif isinstance(data, list):
            return [self.redact_dict(item) for item in data]
        elif isinstance(data, str):
            return self.redact_string(data)
        return data

    def redact_error(self, error_msg):
        return self.redact_string(str(error_msg))

    def safe_log(self, level, msg, *args, **kwargs):
        safe_msg = self.redact_string(msg)
        safe_args = [self.redact_string(str(a)) for a in args]
        getattr(logger, level)(safe_msg, *safe_args, **kwargs)

    def sanitize_for_discord(self, data):
        if isinstance(data, str):
            return self.redact_string(data)
        elif isinstance(data, dict):
            return self.redact_dict(data)
        elif isinstance(data, list):
            return [self.sanitize_for_discord(item) for item in data]
        return data

    def validate_config_display(self, config_data, show_values=False):
        if not show_values:
            return {k: "***" if k.lower() in REDACTED_FIELDS else v
                    for k, v in config_data.items()}
        return self.redact_dict(config_data)

    def check_file_exposure(self, filepath):
        issues = []
        try:
            with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
            for pattern in SECRET_PATTERNS:
                matches = pattern.findall(content)
                if matches:
                    issues.append({
                        "file": filepath,
                        "pattern": pattern.pattern[:50],
                        "matches": len(matches),
                    })
        except (IOError, OSError):
            pass
        return issues

    def scan_data_dir(self):
        issues = []
        if not os.path.isdir(DATA_DIR):
            return issues
        for fname in os.listdir(DATA_DIR):
            if not fname.endswith(".json"):
                continue
            fpath = os.path.join(DATA_DIR, fname)
            file_issues = self.check_file_exposure(fpath)
            issues.extend(file_issues)
        return issues

    def get_status(self):
        with self._lock:
            return {
                "redactions_total": self._redactions,
                "active": True,
            }


secret_guard = SecretGuard()

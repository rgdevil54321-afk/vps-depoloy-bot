"""Dependency and Security Checks system.

Checks installed dependencies for known security issues, tracks dependency
versions, alerts about outdated/vulnerable deps, keeps security checks
separate from production, and does not auto-upgrade critical dependencies.
"""
import os
import sys
import json
import logging
import subprocess
from datetime import datetime, timezone

from core.services import metrics, bus, load_json, save_json, DATA_DIR

logger = logging.getLogger("turtle.dep_check")

DEP_CHECK_PATH = os.path.join(DATA_DIR, "dep_security.json")

KNOWN_VULNERABLE = {
    "requests": {"below": "2.31.0", "cve": "CVE-2023-32681"},
    "urllib3": {"below": "2.0.7", "cve": "multiple"},
    "cryptography": {"below": "41.0.0", "cve": "multiple"},
    "pillow": {"below": "10.0.0", "cve": "multiple"},
    "aiohttp": {"below": "3.8.5", "cve": "CVE-2023-37276"},
}

CRITICAL_DEPS = {"discord.py", "aiohttp", "cryptography", "requests"}


class DependencyChecker:
    def __init__(self):
        self._state = load_json(DEP_CHECK_PATH, {
            "last_check": None,
            "last_results": [],
            "alert_count": 0,
            "versions": {},
        })
        self._known_versions = self._get_installed_versions()
        logger.info("DependencyChecker initialized")

    def _get_installed_versions(self):
        versions = {}
        try:
            proc = subprocess.run(
                [sys.executable, "-m", "pip", "list", "--format=json"],
                capture_output=True, text=True, timeout=30,
            )
            if proc.returncode == 0:
                for pkg in json.loads(proc.stdout):
                    versions[pkg["name"].lower()] = pkg["version"]
        except Exception as e:
            logger.warning("Failed to get pip list: %s", e)
        self._state["versions"] = versions
        return versions

    def check_vulnerabilities(self):
        self._known_versions = self._get_installed_versions()
        results = []
        for dep, info in KNOWN_VULNERABLE.items():
            installed = self._known_versions.get(dep.lower())
            if not installed:
                continue
            from packaging.version import Version
            try:
                if Version(installed) < Version(info["below"]):
                    results.append({
                        "package": dep, "installed": installed,
                        "recommended": info["below"], "cve": info["cve"],
                        "severity": "critical" if dep in CRITICAL_DEPS else "warning",
                    })
            except Exception:
                pass
        self._state["last_check"] = datetime.now(timezone.utc).isoformat()
        self._state["last_results"] = results
        self._save()
        if results:
            self._state["alert_count"] = self._state.get("alert_count", 0) + len(results)
            for r in results:
                bus.emit("dep_check.vulnerability", r)
                logger.warning("Vulnerable dep: %s %s (%s)", r["package"], r["installed"], r["cve"])
        metrics.set("dep_check.vulnerabilities", len(results))
        return results

    def check_outdated(self):
        outdated = []
        try:
            proc = subprocess.run(
                [sys.executable, "-m", "pip", "list", "--outdated", "--format=json"],
                capture_output=True, text=True, timeout=60,
            )
            if proc.returncode == 0:
                for pkg in json.loads(proc.stdout):
                    outdated.append({
                        "package": pkg["name"],
                        "current": pkg["version"],
                        "latest": pkg["latest_version"],
                        "critical": pkg["name"].lower() in {d.lower() for d in CRITICAL_DEPS},
                    })
        except Exception as e:
            logger.warning("Failed to check outdated: %s", e)
        return outdated

    def get_dependency_map(self):
        return dict(self._known_versions)

    def get_status(self):
        return {
            "last_check": self._state.get("last_check"),
            "vulnerabilities_found": len(self._state.get("last_results", [])),
            "total_alerts": self._state.get("alert_count", 0),
            "tracked_deps": len(self._known_versions),
            "critical_deps": len(CRITICAL_DEPS),
        }

    def _save(self):
        save_json(DEP_CHECK_PATH, self._state)


dep_checker = DependencyChecker()

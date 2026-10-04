"""Predictive Monitoring & Capacity Prediction for Turtle Nodes."""
import os
import time
import json
import logging
from datetime import datetime, timedelta, timezone

from core.services import metrics, load_json, save_json, DATA_DIR

logger = logging.getLogger("turtle.predictive")

METRICS_PATH = os.path.join(DATA_DIR, "metric_history.json")


class PredictiveMonitor:
    """Detects trends and predicts threshold breaches for system metrics."""

    def __init__(self):
        self._history = load_json(METRICS_PATH, default={})

    def _save(self):
        save_json(METRICS_PATH, self._history)

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------

    def record_metric(self, name, value):
        """Append a timestamped data point; keeps last 1000 per metric."""
        if name not in self._history:
            self._history[name] = []
        self._history[name].append({"timestamp": time.time(), "value": float(value)})
        if len(self._history[name]) > 1000:
            self._history[name] = self._history[name][-1000:]
        self._save()

    def record_vps_snapshot(self):
        """Record current disk/RAM/CPU/VPS count as metrics (call periodically)."""
        try:
            from core.services import metrics as _m
            gauges = _m.all_gauges()
            mapping = {
                "disk_used_percent": "disk_used_percent",
                "ram_used_percent": "ram_used_percent",
                "cpu_percent": "cpu_percent",
                "vps_count": "vps_count",
                "backup_size": "backup_size_mb",
            }
            for metric_key, gauge_key in mapping.items():
                val = gauges.get(gauge_key)
                if val is not None:
                    self.record_metric(metric_key, val)
            logger.debug("VPS snapshot recorded")
        except Exception as e:
            logger.error("record_vps_snapshot failed: %s", e)

    # ------------------------------------------------------------------
    # History access
    # ------------------------------------------------------------------

    def get_history(self, name, hours=24):
        """Return data points from the last N hours."""
        cutoff = time.time() - hours * 3600
        return [
            p for p in self._history.get(name, [])
            if p["timestamp"] >= cutoff
        ]

    # ------------------------------------------------------------------
    # Trend analysis (linear regression)
    # ------------------------------------------------------------------

    def get_trend(self, name):
        """Compute trend via linear regression on the last 100 data points.

        Returns {direction, rate_per_hour, confidence}.
        """
        points = self._history.get(name, [])[-100:]
        if len(points) < 3:
            return {"direction": "stable", "rate_per_hour": 0.0, "confidence": 0.0}

        n = len(points)
        xs = [p["timestamp"] for p in points]
        ys = [p["value"] for p in points]

        sum_x = sum(xs)
        sum_y = sum(ys)
        sum_xy = sum(x * y for x, y in zip(xs, ys))
        sum_x2 = sum(x * x for x in xs)
        sum_y2 = sum(y * y for y in ys)

        denom = n * sum_x2 - sum_x * sum_x
        if denom == 0:
            return {"direction": "stable", "rate_per_hour": 0.0, "confidence": 0.0}

        slope = (n * sum_xy - sum_x * sum_y) / denom
        intercept = (sum_y - slope * sum_x) / n

        # R² goodness of fit
        ss_res = sum((y - (slope * x + intercept)) ** 2 for x, y in zip(xs, ys))
        mean_y = sum_y / n
        ss_tot = sum((y - mean_y) ** 2 for y in ys)
        r_squared = 1 - (ss_res / ss_tot) if ss_tot != 0 else 0.0
        r_squared = max(0.0, min(1.0, r_squared))

        rate_per_hour = slope * 3600

        if abs(rate_per_hour) < 0.01:
            direction = "stable"
        elif rate_per_hour > 0:
            direction = "up"
        else:
            direction = "down"

        return {
            "direction": direction,
            "rate_per_hour": round(rate_per_hour, 6),
            "confidence": round(r_squared, 4),
        }

    # ------------------------------------------------------------------
    # Threshold breach prediction
    # ------------------------------------------------------------------

    def predict_threshold_breach(self, name, threshold):
        """Predict when a metric will cross a threshold.

        Returns dict with hours_until_breach, predicted_date, etc. or None.
        """
        trend = self.get_trend(name)
        if trend["direction"] != "up":
            return None

        history = self._history.get(name, [])
        if not history:
            return None

        current_value = history[-1]["value"]
        rate_per_hour = trend["rate_per_hour"]

        if current_value >= threshold or rate_per_hour <= 0:
            return None

        hours_until_breach = (threshold - current_value) / rate_per_hour
        if hours_until_breach <= 0:
            return None

        predicted_date = datetime.now(timezone.utc) + timedelta(hours=hours_until_breach)

        return {
            "hours_until_breach": round(hours_until_breach, 2),
            "predicted_date": predicted_date.isoformat(),
            "current_value": round(current_value, 2),
            "threshold": threshold,
            "confidence": trend["confidence"],
        }

    # ------------------------------------------------------------------
    # Aggregate predictions
    # ------------------------------------------------------------------

    def get_predictions(self):
        """Return predictions for all key metrics at their thresholds."""
        checks = [
            ("disk_used_percent", [85, 95]),
            ("ram_used_percent", [90]),
            ("cpu_percent", [90]),
            ("vps_count", [self._get_max_vps_capacity()]),
            ("backup_size", [self._get_disk_limit()]),
        ]
        results = []
        for metric_name, thresholds in checks:
            for thresh in thresholds:
                if thresh is None or thresh <= 0:
                    continue
                pred = self.predict_threshold_breach(metric_name, thresh)
                if pred:
                    pred["metric"] = metric_name
                    results.append(pred)
        return results

    def _get_max_vps_capacity(self):
        """Return node max VPS capacity from gauges or default."""
        try:
            val = metrics.get("max_vps_capacity", 0)
            return val if val and val > 0 else 50
        except Exception:
            return 50

    def _get_disk_limit(self):
        """Return disk limit in MB from gauges or default."""
        try:
            val = metrics.get("disk_limit_mb", 0)
            return val if val and val > 0 else 50000
        except Exception:
            return 50000

    # ------------------------------------------------------------------
    # Disk-specific risk
    # ------------------------------------------------------------------

    def get_disk_risk(self):
        """Analyze disk usage risk with growth rate and breach estimates."""
        history = self._history.get("disk_used_percent", [])
        if not history:
            return {"current": 0, "growth_per_day": 0, "days_to_85": None,
                    "days_to_95": None, "risk_level": "low"}

        current = history[-1]["value"]
        trend = self.get_trend("disk_used_percent")
        growth_per_day = trend["rate_per_hour"] * 24

        days_to_85 = self._days_to_threshold(current, growth_per_day, 85)
        days_to_95 = self._days_to_threshold(current, growth_per_day, 95)

        if current >= 95 or (days_to_95 is not None and days_to_95 <= 1):
            risk_level = "critical"
        elif current >= 85 or (days_to_95 is not None and days_to_95 <= 3):
            risk_level = "high"
        elif days_to_85 is not None and days_to_85 <= 7:
            risk_level = "medium"
        else:
            risk_level = "low"

        return {
            "current": round(current, 2),
            "growth_per_day": round(growth_per_day, 4),
            "days_to_85": round(days_to_85, 1) if days_to_85 is not None else None,
            "days_to_95": round(days_to_95, 1) if days_to_95 is not None else None,
            "risk_level": risk_level,
        }

    def _days_to_threshold(self, current, growth_per_day, threshold):
        """Calculate days until threshold is reached given a growth rate."""
        if growth_per_day <= 0 or current >= threshold:
            return None
        return (threshold - current) / growth_per_day

    # ------------------------------------------------------------------
    # Capacity forecast
    # ------------------------------------------------------------------

    def get_capacity_forecast(self):
        """Forecast capacity for each metric at 7d and 30d horizons."""
        metric_defs = [
            ("disk_used_percent", 100),
            ("ram_used_percent", 100),
            ("cpu_percent", 100),
            ("vps_count", self._get_max_vps_capacity()),
        ]
        forecasts = {}
        overall_risk = "low"

        for name, ceiling in metric_defs:
            history = self._history.get(name, [])
            if not history:
                forecasts[name] = {
                    "current": 0, "predicted_7d": 0, "predicted_30d": 0,
                    "headroom_days": None,
                }
                continue

            current = history[-1]["value"]
            trend = self.get_trend(name)
            rate_per_day = trend["rate_per_hour"] * 24

            predicted_7d = current + rate_per_day * 7
            predicted_30d = current + rate_per_day * 30

            headroom_days = None
            if rate_per_day > 0 and current < ceiling:
                headroom_days = (ceiling - current) / rate_per_day

            # Determine risk from headroom
            if headroom_days is not None:
                if headroom_days <= 3:
                    risk = "critical"
                elif headroom_days <= 7:
                    risk = "high"
                elif headroom_days <= 14:
                    risk = "medium"
                else:
                    risk = "low"

                risk_order = {"low": 0, "medium": 1, "high": 2, "critical": 3}
                if risk_order.get(risk, 0) > risk_order.get(overall_risk, 0):
                    overall_risk = risk

            forecasts[name] = {
                "current": round(current, 2),
                "predicted_7d": round(min(predicted_7d, ceiling * 1.5), 2),
                "predicted_30d": round(min(predicted_30d, ceiling * 1.5), 2),
                "headroom_days": round(headroom_days, 1) if headroom_days is not None else None,
            }

        return {
            "metrics": forecasts,
            "overall_risk": overall_risk,
        }

    # ------------------------------------------------------------------
    # Formatting
    # ------------------------------------------------------------------

    def format_prediction(self, prediction):
        """Format a prediction dict into a human-readable alert string."""
        metric = prediction.get("metric", "unknown")
        current = prediction.get("current_value", 0)
        threshold = prediction.get("threshold", 0)
        hours = prediction.get("hours_until_breach", 0)
        confidence = prediction.get("confidence", 0)

        if hours < 24:
            time_str = f"~{hours:.0f} hours"
        else:
            days = hours / 24
            time_str = f"~{days:.1f} days"

        if hours <= 6:
            icon = "\U0001f534"
            severity = "Critical"
        elif hours <= 24:
            icon = "\U0001f7e0"
            severity = "Warning"
        else:
            icon = "\u26a0\ufe0f"
            severity = "Alert"

        metric_labels = {
            "disk_used_percent": "Disk",
            "ram_used_percent": "RAM",
            "cpu_percent": "CPU",
            "vps_count": "VPS Count",
            "backup_size": "Backup Size",
        }
        label = metric_labels.get(metric, metric)

        trend = self.get_trend(metric)
        growth = trend["rate_per_hour"] * 24
        growth_str = f"+{growth:.1f}%/day" if growth > 0 else f"{growth:.1f}%/day"

        return (
            f"{icon} {severity} {label}: Current {current:.1f}%, "
            f"Growth {growth_str}, "
            f"Threshold ({threshold:.0f}%) in {time_str} "
            f"(confidence {confidence:.0%})"
        )

    # ------------------------------------------------------------------
    # Recommendations
    # ------------------------------------------------------------------

    def get_recommendations(self):
        """Generate actionable recommendations based on current predictions."""
        recommendations = []
        disk_risk = self.get_disk_risk()
        capacity = self.get_capacity_forecast()
        predictions = self.get_predictions()

        # Disk-based recommendations
        if disk_risk["risk_level"] == "critical":
            recommendations.append(
                "URGENT: Disk usage critical. Run purge to free space immediately."
            )
        elif disk_risk["risk_level"] == "high":
            recommendations.append(
                "Disk usage high. Schedule purge and review backup retention."
            )
        elif disk_risk["risk_level"] == "medium":
            recommendations.append(
                "Disk growing steadily. Plan cleanup within the next week."
            )

        if disk_risk["growth_per_day"] > 5:
            recommendations.append(
                "Abnormal disk growth detected. Check for runaway logs or temp files."
            )

        # RAM recommendations
        ram_trend = self.get_trend("ram_used_percent")
        if ram_trend["direction"] == "up" and ram_trend["rate_per_hour"] > 0.5:
            recommendations.append(
                "RAM usage trending upward. Check for memory leaks in VPS workloads."
            )

        ram_breach = self.predict_threshold_breach("ram_used_percent", 90)
        if ram_breach and ram_breach["hours_until_breach"] < 48:
            recommendations.append(
                f"RAM approaching 90% in ~{ram_breach['hours_until_breach']:.0f}h. "
                "Consider migrating or scaling VPS instances."
            )

        # CPU recommendations
        cpu_breach = self.predict_threshold_breach("cpu_percent", 90)
        if cpu_breach and cpu_breach["hours_until_breach"] < 24:
            recommendations.append(
                "CPU sustained high load. Review busiest VPS instances for optimization."
            )

        # VPS count recommendations
        max_vps = self._get_max_vps_capacity()
        vps_pred = self.predict_threshold_breach("vps_count", max_vps * 0.9)
        if vps_pred:
            recommendations.append(
                f"Approaching VPS capacity limit ({max_vps}). "
                "Plan node expansion or offload inactive VPS."
            )

        # Backup size recommendations
        backup_pred = self.predict_threshold_breach("backup_size", self._get_disk_limit() * 0.8)
        if backup_pred:
            recommendations.append(
                "Backup storage growing. Review retention policy and prune old backups."
            )

        # General capacity
        if capacity["overall_risk"] == "critical":
            recommendations.append(
                "Overall node capacity critical. Immediate intervention required."
            )
        elif capacity["overall_risk"] == "high":
            recommendations.append(
                "Node capacity under stress. Review all metrics and plan mitigation."
            )

        if not recommendations:
            recommendations.append("All metrics within normal ranges. No action needed.")

        return recommendations

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------

    def get_summary(self):
        """Return a full summary dict for dashboards or embeds."""
        disk_risk = self.get_disk_risk()
        capacity = self.get_capacity_forecast()
        predictions = self.get_predictions()
        recommendations = self.get_recommendations()

        formatted = []
        for p in predictions:
            formatted.append(self.format_prediction(p))

        return {
            "disk_risk": disk_risk,
            "capacity_forecast": capacity,
            "active_predictions": predictions,
            "formatted_predictions": formatted,
            "recommendations": recommendations,
            "total_metrics_tracked": len(self._history),
        }

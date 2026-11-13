"""Structured project data (budget, schedule, incidents) and the analytics the read tools expose.

The repository reads the synthetic CSV/JSON files. All arithmetic happens here, in code, so the
model never has to add up numbers itself: it receives computed totals and variances.
"""

from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

AMBER_COST_PCT = 5.0
RED_COST_PCT = 10.0
AMBER_DELAY_DAYS = 14
RED_DELAY_DAYS = 21


class UnknownProjectError(KeyError):
    pass


@dataclass(frozen=True)
class BudgetLine:
    project_id: str
    cost_code: str
    description: str
    budget: int
    actual: int
    committed: int
    forecast: int

    @property
    def variance(self) -> int:
        return self.forecast - self.budget

    @property
    def variance_pct(self) -> float:
        return round(100 * self.variance / self.budget, 1) if self.budget else 0.0


@dataclass(frozen=True)
class Milestone:
    project_id: str
    name: str
    baseline: date
    forecast: date
    status: str

    @property
    def delay_days(self) -> int:
        return (self.forecast - self.baseline).days


def _rating(value: float, amber: float, red: float) -> str:
    return "red" if value >= red else "amber" if value >= amber else "green"


class ProjectRepository:
    def __init__(self, data_dir: Path) -> None:
        meta = json.loads((data_dir / "projects.json").read_text(encoding="utf-8"))
        self.company: str = meta["company"]
        self.reporting_date: str = meta["reporting_date"]
        self._projects: dict[str, dict[str, Any]] = {p["project_id"]: p for p in meta["projects"]}
        self._budget = [
            BudgetLine(
                r["project_id"],
                r["cost_code"],
                r["description"],
                int(r["budget_eur"]),
                int(r["actual_eur"]),
                int(r["committed_eur"]),
                int(r["forecast_eur"]),
            )
            for r in self._read(data_dir / "budget_lines.csv")
        ]
        self._milestones = [
            Milestone(
                r["project_id"],
                r["milestone"],
                date.fromisoformat(r["baseline_date"]),
                date.fromisoformat(r["forecast_date"]),
                r["status"],
            )
            for r in self._read(data_dir / "milestones.csv")
        ]
        self._incidents = self._read(data_dir / "incidents.csv")

    @staticmethod
    def _read(path: Path) -> list[dict[str, str]]:
        with path.open(encoding="utf-8", newline="") as handle:
            return list(csv.DictReader(handle))

    # -- lookups -----------------------------------------------------------------------------------
    def project_ids(self) -> list[str]:
        return sorted(self._projects)

    def project(self, project_id: str) -> dict[str, Any]:
        try:
            return dict(self._projects[project_id])
        except KeyError:
            raise UnknownProjectError(project_id) from None

    def list_projects(self) -> list[dict[str, Any]]:
        return [dict(self._projects[pid]) for pid in self.project_ids()]

    def find_project_id(self, text: str) -> str | None:
        """Resolve a project mentioned by id or by (part of) its name. Used by the offline model."""
        lowered = text.lower()
        words = set(re.findall(r"[a-z]+", lowered))
        for pid, project in self._projects.items():
            if pid.lower() in lowered or words & set(str(project["name"]).lower().split()):
                return pid
        return None

    # -- analytics ---------------------------------------------------------------------------------
    def budget_summary(self, project_id: str) -> dict[str, Any]:
        project = self.project(project_id)
        lines = [line for line in self._budget if line.project_id == project_id]
        budget = sum(line.budget for line in lines)
        forecast = sum(line.forecast for line in lines)
        variance_pct = round(100 * (forecast - budget) / budget, 1)
        return {
            "project_id": project_id,
            "project_name": project["name"],
            "currency": "EUR",
            "as_of": self.reporting_date,
            "budget": budget,
            "actual_to_date": sum(line.actual for line in lines),
            "committed": sum(line.committed for line in lines),
            "forecast_at_completion": forecast,
            "forecast_variance": forecast - budget,
            "forecast_variance_pct": variance_pct,
            "rating": _rating(variance_pct, AMBER_COST_PCT, RED_COST_PCT),
        }

    def cost_variances(self, project_id: str, min_variance_pct: float = 0.0) -> dict[str, Any]:
        self.project(project_id)
        lines = sorted(
            (
                line
                for line in self._budget
                if line.project_id == project_id and abs(line.variance_pct) >= min_variance_pct
            ),
            key=lambda line: -line.variance_pct,
        )
        return {
            "project_id": project_id,
            "currency": "EUR",
            "as_of": self.reporting_date,
            "min_variance_pct": min_variance_pct,
            "lines": [
                {
                    "cost_code": line.cost_code,
                    "description": line.description,
                    "budget": line.budget,
                    "forecast_at_completion": line.forecast,
                    "variance": line.variance,
                    "variance_pct": line.variance_pct,
                    "rating": _rating(line.variance_pct, AMBER_COST_PCT, RED_COST_PCT),
                }
                for line in lines
            ],
        }

    def schedule_status(self, project_id: str) -> dict[str, Any]:
        self.project(project_id)
        return {
            "project_id": project_id,
            "as_of": self.reporting_date,
            "milestones": [
                {
                    "milestone": m.name,
                    "baseline_date": m.baseline.isoformat(),
                    "forecast_date": m.forecast.isoformat(),
                    "delay_days": m.delay_days,
                    "status": m.status,
                    "rating": _rating(m.delay_days, AMBER_DELAY_DAYS, RED_DELAY_DAYS),
                }
                for m in self._milestones
                if m.project_id == project_id
            ],
        }

    def open_incidents(self, project_id: str) -> list[dict[str, str]]:
        return [dict(i) for i in self._incidents if i["project_id"] == project_id and i["status"] == "open"]

    def detect_risks(self, project_id: str) -> dict[str, Any]:
        """Rule-based risk register built from the cost, schedule and incident data."""
        summary = self.budget_summary(project_id)
        risks: list[dict[str, Any]] = []
        if summary["rating"] != "green":
            risks.append(
                {
                    "category": "cost",
                    "severity": summary["rating"],
                    "title": f"Project forecast is {summary['forecast_variance_pct']}% over budget",
                    "evidence": f"Forecast {summary['forecast_at_completion']} EUR against budget "
                    f"{summary['budget']} EUR.",
                }
            )
        for line in self.cost_variances(project_id, AMBER_COST_PCT)["lines"]:
            if line["variance"] > 0:
                risks.append(
                    {
                        "category": "cost",
                        "severity": line["rating"],
                        "title": f"{line['description']} ({line['cost_code']}) is {line['variance_pct']}% over budget",
                        "evidence": f"Forecast {line['forecast_at_completion']} EUR against budget "
                        f"{line['budget']} EUR.",
                    }
                )
        for milestone in self.schedule_status(project_id)["milestones"]:
            if milestone["status"] != "done" and milestone["rating"] != "green":
                risks.append(
                    {
                        "category": "schedule",
                        "severity": milestone["rating"],
                        "title": f"{milestone['milestone']} is forecast {milestone['delay_days']} days late",
                        "evidence": f"Baseline {milestone['baseline_date']}, forecast {milestone['forecast_date']}.",
                    }
                )
        for incident in self.open_incidents(project_id):
            risks.append(
                {
                    "category": "hse",
                    "severity": "amber" if incident["severity"] in ("low", "medium") else "red",
                    "title": f"Open incident {incident['incident_id']}",
                    "evidence": incident["description"],
                }
            )
        order = {"red": 0, "amber": 1}
        risks.sort(key=lambda risk: order[risk["severity"]])
        return {"project_id": project_id, "as_of": self.reporting_date, "risks": risks}

    def status_report(self, project_id: str) -> dict[str, Any]:
        """Draft weekly status report following the company standard (five sections)."""
        project = self.project(project_id)
        summary = self.budget_summary(project_id)
        variances = self.cost_variances(project_id, AMBER_COST_PCT)["lines"]
        milestones = self.schedule_status(project_id)["milestones"]
        risks = self.detect_risks(project_id)["risks"]
        overall = "red" if any(r["severity"] == "red" for r in risks) else "amber" if risks else "green"

        def money(value: int) -> str:
            return f"{value:,} EUR"

        lines = [
            f"# Weekly status report (DRAFT) - {project['name']} ({project_id})",
            f"Reporting date: {self.reporting_date}. Project manager: {project['project_manager']}. "
            f"Client: {project['client']}.",
            "",
            "## Summary",
            f"Overall rating: {overall.upper()}. {len(risks)} open risk(s) identified from cost, schedule "
            "and incident data.",
            "",
            "## Cost",
            f"Budget {money(summary['budget'])}; actual to date {money(summary['actual_to_date'])}; forecast at "
            f"completion {money(summary['forecast_at_completion'])} ({summary['forecast_variance_pct']:+}% "
            f"against budget, rated {summary['rating']}).",
            *[
                f"- {v['description']} ({v['cost_code']}): {v['variance_pct']:+}% ({money(v['variance'])}), "
                f"rated {v['rating']}."
                for v in variances
            ],
            "",
            "## Schedule",
            *[
                f"- {m['milestone']}: baseline {m['baseline_date']}, forecast {m['forecast_date']} "
                f"({m['delay_days']:+} days, {m['status']})."
                for m in milestones
            ],
            "",
            "## Risks",
            *([f"- [{r['severity'].upper()}] {r['title']}. {r['evidence']}" for r in risks] or ["- None."]),
            "",
            "## Decisions needed",
            "- To be completed by the project manager before issue.",
        ]
        return {
            "project_id": project_id,
            "as_of": self.reporting_date,
            "overall_rating": overall,
            "is_draft": True,
            "report_markdown": "\n".join(lines),
        }

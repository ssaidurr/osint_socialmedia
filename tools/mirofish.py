#!/usr/bin/env python3
"""Drive a local MiroFish run from the command line, seed to report.

MiroFish ships a web UI and no CLI, but its Flask backend exposes the whole pipeline over
HTTP, so the nine clicks are nine calls and a run can be scripted — which is what makes it
usable from a scheduled job rather than only by hand. The sequence, and the field names, were
read from backend/app/api/{graph,simulation,report}.py rather than from the README:

    POST /api/graph/ontology/generate   multipart: files, simulation_requirement, project_name
    POST /api/graph/build               → task_id, polled at GET /api/graph/task/<id>
    POST /api/simulation/create         → simulation_id
    POST /api/simulation/prepare        → task_id, polled at POST /api/simulation/prepare/status
    POST /api/simulation/start          → polled at GET /api/simulation/<id>/run-status
    POST /api/report/generate           → report_id, polled at POST /api/report/generate/status
    GET  /api/report/<report_id>        → markdown_content

Every response is {"success": bool, "data": {...}} and every background task reports one of
pending / processing / completed / failed.

    python tools/seed_pack.py --country IR --days 30 --out seed-IR.md
    python tools/mirofish.py --seed seed-IR.md --rounds 10 --platform reddit \
        --ask "How does opinion on this move over the next month, and what breaks it?" \
        --out report-IR.md

Note that the backend has no authentication of any kind. Point --base at localhost, and if this
ever runs against a host that is not this machine, put it behind something that does.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import requests

POLL_EVERY = 5


class Failed(RuntimeError):
    pass


class Run:
    def __init__(self, base: str, patience: int):
        self.base = base.rstrip("/")
        self.patience = patience          # seconds to wait on any one stage

    def call(self, method: str, path: str, **kw) -> dict:
        try:
            reply = requests.request(method, f"{self.base}{path}", timeout=120, **kw)
        except requests.RequestException as e:
            raise Failed(f"{path}: {e}") from None
        try:
            body = reply.json()
        except ValueError:
            raise Failed(f"{path}: HTTP {reply.status_code}, and the body was not JSON") from None
        if not body.get("success"):
            raise Failed(f"{path}: HTTP {reply.status_code} — {body.get('error') or body}")
        return body.get("data") or {}

    def wait(self, what: str, check) -> dict:
        """Call `check` every few seconds until it reports done, or patience runs out.

        `check` returns (done, note) — the note is printed whenever it changes, so a run that
        takes twenty minutes still says what it is doing.
        """
        started, said = time.time(), None
        while time.time() - started < self.patience:
            done, note, data = check()
            if note and note != said:
                print(f"    {int(time.time() - started):>5}s  {note}")
                said = note
            if done:
                return data
            time.sleep(POLL_EVERY)
        raise Failed(f"{what} was still going after {self.patience}s — raise --patience")


def task_check(run: Run, method: str, path: str, body: dict | None = None):
    """A check function for the three endpoints that answer with a Task."""
    def check():
        data = (run.call(method, path, json=body) if body is not None
                else run.call(method, path))
        state = data.get("status")
        note = f"{state} · {data.get('progress', '')}{'%' if data.get('progress') is not None else ''} {data.get('message', '')}".strip()
        if state == "failed":
            raise Failed(data.get("error") or "the task failed without saying why")
        return state == "completed", note, data
    return check


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--seed", required=True, help="the .md/.txt/.pdf briefing to seed the world with")
    p.add_argument("--ask", required=True, help="the question, in plain language")
    p.add_argument("--name", default="", help="project name; defaults to the seed's filename")
    p.add_argument("--context", default="", help="anything else the engine should know")
    p.add_argument("--rounds", type=int, default=10, help="max simulation rounds (their own advice: start under 40)")
    p.add_argument("--platform", default="reddit", choices=("reddit", "twitter", "parallel"))
    p.add_argument("--base", default="http://localhost:5001")
    p.add_argument("--patience", type=int, default=3600, help="seconds to wait on any one stage")
    p.add_argument("--out", default="report.md")
    a = p.parse_args()

    seed = Path(a.seed)
    if not seed.exists():
        print(f"No seed at {seed}", file=sys.stderr)
        return 1
    if seed.suffix.lower() not in (".md", ".txt", ".pdf", ".markdown"):
        print(f"{seed.suffix} is not one of the types it accepts (.md .txt .pdf)", file=sys.stderr)
        return 1

    run = Run(a.base, a.patience)
    try:
        requests.get(f"{run.base}/health", timeout=10)
    except requests.RequestException:
        print(f"Nothing is answering at {run.base}. Is the container up?", file=sys.stderr)
        return 1

    try:
        print(f"  seeding from {seed.name} ({seed.stat().st_size / 1024:,.0f} KB)")
        with seed.open("rb") as fh:
            data = run.call("POST", "/api/graph/ontology/generate",
                            files={"files": (seed.name, fh)},
                            data={"simulation_requirement": a.ask,
                                  "project_name": a.name or seed.stem,
                                  "additional_context": a.context})
        project = data["project_id"]
        print(f"  project {project}")

        print("  building the knowledge graph")
        task = run.call("POST", "/api/graph/build", json={"project_id": project})["task_id"]
        run.wait("the graph build", task_check(run, "GET", f"/api/graph/task/{task}"))

        sim = run.call("POST", "/api/simulation/create", json={"project_id": project})["simulation_id"]
        print(f"  simulation {sim}")

        print("  writing the agent personas")
        task = run.call("POST", "/api/simulation/prepare", json={"simulation_id": sim})["task_id"]
        run.wait("persona preparation",
                 task_check(run, "POST", "/api/simulation/prepare/status", {"task_id": task}))

        print(f"  running {a.rounds} rounds on {a.platform}")
        run.call("POST", "/api/simulation/start",
                 json={"simulation_id": sim, "platform": a.platform, "max_rounds": a.rounds})

        def running():
            data = run.call("GET", f"/api/simulation/{sim}/run-status")
            state = data.get("runner_status", "")
            return state in ("completed", "stopped", "finished"), state, data
        run.wait("the simulation", running)

        print("  writing the report")
        data = run.call("POST", "/api/report/generate", json={"simulation_id": sim})
        report = data["report_id"]
        run.wait("the report",
                 task_check(run, "POST", "/api/report/generate/status", {"task_id": data["task_id"]}))

        text = run.call("GET", f"/api/report/{report}").get("markdown_content") or ""
    except Failed as e:
        print(f"\n  stopped: {e}", file=sys.stderr)
        return 1
    except KeyError as e:
        print(f"\n  the API answered without {e} — the pipeline may have changed", file=sys.stderr)
        return 1

    Path(a.out).write_text(text)
    print(f"\n  {a.out}  {len(text) / 1024:,.0f} KB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

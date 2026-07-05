"""``mir-report`` — a status dashboard generated from the official APIs.

Collects everything from documented endpoints only — robot: ``/status``,
``/missions``, ``/mission_queue``, ``/log/error_reports``,
``/statistics/distance``; fleet: ``/robots``, ``/robots/{id}``, ``/order`` —
and renders a self-contained HTML dashboard: current-status indicators, the
daily trend, and a descriptive timeline of actions. Works against real
robots and the emulator alike; nothing here touches ``/_emulator`` surfaces.

    mir-report http://192.168.12.20 -o robot.html
    mir-report http://127.0.0.1:8090 --api-key <key> -o fleet.html --json

From Python: ``collect_report()`` / ``render_report()`` / ``write_report()``
(async twins where it matters). Pass ``session_id=`` to report on an
emulator session's isolated robot.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from html import escape
from pathlib import Path
from typing import Any

import httpx

from mir_client.auth import DEFAULT_PASSWORD, DEFAULT_USERNAME, robot_token
from mir_client.discovery import detect_server_async

ROBOT_BASE = "/api/v2.0.0"
FLEET_BASE = "/api/v1"
DEFAULT_API_KEY = "distributor"


def _unsupported_kind_message(kind: str) -> str:
    return f"cannot report on a {kind!r} target; point mir-report at a robot or fleet"


async def _get_json(http: httpx.AsyncClient, path: str) -> Any:
    response = await http.get(path)
    response.raise_for_status()
    return response.json()


def _as_list(value: Any) -> list:
    """MiR specs declare some list endpoints with the element's object schema,
    and servers differ on which shape they answer with — accept both."""
    if isinstance(value, list):
        return value
    return [value] if isinstance(value, dict) else []


def _entry_minutes(started: str | None, finished: str | None) -> int | None:
    if not started or not finished:
        return None
    from datetime import datetime

    fmt = "%Y-%m-%dT%H:%M:%S"
    delta = datetime.strptime(finished, fmt) - datetime.strptime(started, fmt)
    return round(delta.total_seconds() / 60)


async def _collect_robot(
    base: str, version: str | None, headers: dict, timeout: float, httpx_kwargs: dict
) -> dict:
    async with httpx.AsyncClient(
        base_url=base, headers=headers, timeout=timeout, **httpx_kwargs
    ) as http:
        status = await _get_json(http, f"{ROBOT_BASE}/status")
        mission_names = {
            m["guid"]: m["name"] for m in _as_list(await _get_json(http, f"{ROBOT_BASE}/missions"))
        }
        queue: list[dict] = []
        for item in _as_list(await _get_json(http, f"{ROBOT_BASE}/mission_queue")):
            queue.append(await _get_json(http, f"{ROBOT_BASE}/mission_queue/{item['id']}"))
        error_reports = await _get_json(http, f"{ROBOT_BASE}/log/error_reports")
        distance = await _get_json(http, f"{ROBOT_BASE}/statistics/distance")

    robot = {
        "name": status.get("robot_name"),
        "model": status.get("robot_model"),
        "state": status.get("state_text"),
        "battery": round(float(status.get("battery_percentage", 0.0)), 1),
        "mission_text": status.get("mission_text"),
        "position": status.get("position", {}),
        # code 0 entries are schema placeholders, not live errors
        "errors": [e for e in status.get("errors", []) if e.get("code")],
        "uptime_s": status.get("uptime"),
    }

    timeline = []
    for entry in queue:
        name = mission_names.get(entry.get("mission_id", ""), entry.get("mission_id", "?"))
        state = entry.get("state", "?")
        when = entry.get("finished") or entry.get("started") or entry.get("ordered")
        minutes = _entry_minutes(entry.get("started"), entry.get("finished"))
        text = f"Mission '{name}' — {state}" + (f" ({minutes} min)" if minutes is not None else "")
        timeline.append({"time": when or "", "kind": "mission", "state": state, "text": text})
    for report in _as_list(error_reports):
        module = report.get("module", "?")
        description = report.get("description", "")
        if module == "emulated" and description == "emulated":
            continue  # seeded placeholder, not an event
        timeline.append(
            {
                "time": report.get("time", ""),
                "kind": "error",
                "state": "Error",
                "text": f"{module}: {description}",
            }
        )
    timeline.sort(key=lambda e: e["time"])

    return {
        "kind": "robot",
        "target": base,
        "version": version,
        "robots": [robot],
        "trend_label": "distance driven (m)",
        "trend": [
            {"date": str(d.get("date", ""))[:10], "value": d.get("distance", 0.0)}
            for d in _as_list(distance)
        ],
        "timeline": timeline,
    }


async def _collect_fleet(
    base: str, version: str | None, headers: dict, timeout: float, httpx_kwargs: dict
) -> dict:
    async with httpx.AsyncClient(
        base_url=base, headers=headers, timeout=timeout, **httpx_kwargs
    ) as http:
        listing = await _get_json(http, f"{FLEET_BASE}/robots")
        robots = []
        for identity in listing.get("robots", []):
            detail = await _get_json(http, f"{FLEET_BASE}/robots/{identity['robot-id']}")
            runtime = detail.get("runtime-data", {})
            robots.append(
                {
                    "name": identity.get("name"),
                    "model": identity.get("model"),
                    "state": None,  # the Fleet API reports runtime data, not a scalar state
                    "battery": round(float(runtime.get("battery-percentage", 0.0)), 1),
                    "mission_text": None,
                    "position": runtime.get("pose", {}),
                    "errors": [],
                    "uptime_s": None,
                }
            )
        orders = await _get_json(http, f"{FLEET_BASE}/order")

    timeline = []
    per_day: dict[str, int] = {}
    for order in _as_list(orders):
        when = order.get("order-queued") or order.get("order-created") or ""
        per_day[when[:10]] = per_day.get(when[:10], 0) + 1
        timeline.append(
            {
                "time": when,
                "kind": "order",
                "state": order.get("order-status", "?"),
                "text": (
                    f"Order {order.get('order-id', '?')[:13]}… — "
                    f"mission '{order.get('mission-name', '?')}' on "
                    f"{order.get('robot-name', 'unassigned')} · {order.get('order-status', '?')}"
                ),
            }
        )
    timeline.sort(key=lambda e: e["time"])

    return {
        "kind": "fleet",
        "target": base,
        "version": version,
        "robots": robots,
        "trend_label": "orders per day",
        "trend": [{"date": day, "value": n} for day, n in sorted(per_day.items())],
        "timeline": timeline,
    }


async def collect_report_async(
    base_url: str,
    *,
    username: str = DEFAULT_USERNAME,
    password: str = DEFAULT_PASSWORD,
    api_key: str = DEFAULT_API_KEY,
    session_id: str | None = None,
    timeout: float = 10.0,
    **httpx_kwargs: Any,
) -> dict:
    """Report data for the robot or fleet at *base_url*, official endpoints only."""
    base = base_url.rstrip("/")
    info = await detect_server_async(base, api_key=api_key, timeout=timeout, **httpx_kwargs)
    session = {"X-MiR-Session": session_id} if session_id else {}
    if info.kind == "robot":
        headers = {"Authorization": f"Basic {robot_token(username, password)}", **session}
        return await _collect_robot(base, info.version, headers, timeout, httpx_kwargs)
    if info.kind == "fleet":
        headers = {"x-api-key": api_key, **session}
        return await _collect_fleet(base, info.version, headers, timeout, httpx_kwargs)
    raise ValueError(_unsupported_kind_message(info.kind))


def collect_report(base_url: str, **kwargs: Any) -> dict:
    """Sync twin of :func:`collect_report_async`."""
    return asyncio.run(collect_report_async(base_url, **kwargs))


# --- rendering -----------------------------------------------------------------
# Same brand system as the project's docs pages, self-contained (no external
# fonts or scripts) so the file works offline and in restricted viewers.

_STATE_LED = {
    "Ready": "#1d9e61",
    "Executing": "#1a76bc",
    "Pause": "#c77700",
    "Manualcontrol": "#c77700",
    "EmergencyStop": "#cf3228",
    "Error": "#cf3228",
}

_CSS = """
:root { --brand:#00bfff; --blue:#1a76bc; --blue-deep:#105aa8; --ink:#0c0931;
  --ink-dim:#565b74; --ink-faint:#8a8fa5; --tint:#effafe; --tint-2:#cbeefa;
  --line:#e4e9f0; --green:#1d9e61; --amber:#c77700; --red:#cf3228;
  --mono:ui-monospace,SFMono-Regular,Menlo,monospace;
  --sans:-apple-system,"Segoe UI",Helvetica,Arial,sans-serif; }
*{box-sizing:border-box;margin:0;padding:0}
body{background:#fff;color:var(--ink);font-family:var(--sans);font-size:14px;line-height:1.6}
.brandbar{height:4px;background:var(--brand)}
.wrap{max-width:1080px;margin:0 auto;padding:18px 20px 60px}
h1{font-size:24px;font-weight:300}
h1 span{color:var(--brand)}
.sub{font-size:12.5px;color:var(--ink-dim);margin-bottom:18px}
code{font-family:var(--mono);font-size:.92em;background:var(--tint);
  border:1px solid var(--tint-2);border-radius:4px;padding:0 4px;color:var(--blue-deep)}
.panel{background:#fff;border:1px solid var(--line);border-radius:.4rem;
  box-shadow:0 1px 3px rgba(12,9,49,.06);margin-top:16px;overflow:hidden}
.panel-head{display:flex;align-items:center;gap:10px;padding:11px 16px;
  border-bottom:1px solid var(--line);font-size:15px;font-weight:600}
.panel-head::before{content:"";width:8px;height:8px;border-radius:2px;background:var(--brand)}
.panel-head .tag{margin-left:auto;color:var(--ink-faint);font-size:11px;
  font-family:var(--mono);font-weight:400}
.panel-body{padding:16px}
.bots{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:14px}
.bot{border:1px solid var(--line);border-radius:.4rem;padding:12px 14px}
.bot .nm{font-weight:600;display:flex;align-items:center;gap:8px}
.led{width:9px;height:9px;border-radius:50%;flex:none}
.bot .st{font-size:12px;color:var(--ink-dim)}
.batt{margin-top:8px;height:8px;border-radius:4px;background:var(--tint);
  border:1px solid var(--tint-2);overflow:hidden}
.batt i{display:block;height:100%;background:var(--green)}
.batt.low i{background:var(--red)}
.kv{margin-top:8px;font-size:12px;color:var(--ink-dim);display:grid;gap:2px;font-variant-numeric:tabular-nums}
.err{color:var(--red);font-size:12px;margin-top:6px}
.trend svg{display:block;width:100%;height:auto}
.tl{max-height:420px;overflow-y:auto}
.ev{display:flex;gap:10px;padding:5px 6px;border-radius:6px;font-size:12.5px}
.ev:hover{background:var(--tint)}
.ev .t{color:var(--ink-faint);font-family:var(--mono);font-size:11.5px;flex:0 0 118px}
.ev.error .tx{color:var(--red)}
.ev .tx{color:var(--ink-dim)}
footer{color:var(--ink-faint);font-size:12px;margin-top:18px}
"""


def _render_bot(robot: dict) -> str:
    led = _STATE_LED.get(robot.get("state") or "", "#8a8fa5")
    battery = robot.get("battery", 0.0)
    low = " low" if battery < 20 else ""
    position = robot.get("position") or {}
    pos = f"({position.get('x', '?')}, {position.get('y', '?')})" if position else "—"
    state = robot.get("state") or "—"
    mission = robot.get("mission_text") or "—"
    errors = "".join(
        f'<div class="err">&#9888; {escape(str(e.get("module", "?")))}: '
        f"{escape(str(e.get('description', '')))}</div>"
        for e in robot.get("errors", [])
    )
    return f"""<div class="bot">
  <div class="nm"><span class="led" style="background:{led}"></span>{
        escape(str(robot.get("name", "?")))
    }
    <span style="color:var(--ink-faint);font-weight:400;font-size:11px">{
        escape(str(robot.get("model", "")))
    }</span></div>
  <div class="st">{escape(state)} &middot; {escape(str(mission))}</div>
  <div class="batt{low}"><i style="width:{max(0.0, min(100.0, battery))}%"></i></div>
  <div class="kv"><div>battery {battery}%</div><div>position {escape(pos)}</div></div>
  {errors}
</div>"""


def _render_trend(trend: list[dict], label: str) -> str:
    if not trend:
        return '<p style="color:var(--ink-faint)">no data yet</p>'
    width, height, pad = 720, 150, 26
    bar_zone = width - 2 * pad
    peak = max((d["value"] for d in trend), default=0) or 1
    step = bar_zone / len(trend)
    bar_w = min(56.0, step - 8)
    bars = []
    for i, day in enumerate(trend):
        h = (day["value"] / peak) * (height - 2 * pad)
        bx = pad + i * step + (step - bar_w) / 2
        by = height - pad - h
        bars.append(
            f'<rect x="{bx:.1f}" y="{by:.1f}" width="{bar_w:.1f}" height="{max(h, 1):.1f}" '
            f'rx="4" fill="#1a76bc"><title>{escape(day["date"])}: {day["value"]}</title></rect>'
            f'<text x="{bx + bar_w / 2:.1f}" y="{height - pad + 14}" text-anchor="middle" '
            f'font-size="10" fill="#8a8fa5">{escape(day["date"][5:])}</text>'
            f'<text x="{bx + bar_w / 2:.1f}" y="{by - 5:.1f}" text-anchor="middle" '
            f'font-size="10" fill="#565b74">{round(day["value"])}</text>'
        )
    baseline = (
        f'<line x1="{pad}" y1="{height - pad}" x2="{width - pad}" y2="{height - pad}" '
        f'stroke="#c9d3de" stroke-width="1"/>'
    )
    return (
        f'<div class="trend"><svg viewBox="0 0 {width} {height}" role="img" '
        f'aria-label="{escape(label)}">{baseline}{"".join(bars)}</svg></div>'
    )


def render_report(data: dict) -> str:
    """Self-contained HTML dashboard for a collected report."""
    kind = data.get("kind", "robot")
    robots = data.get("robots", [])
    timeline = data.get("timeline", [])
    icon = {"mission": "&#10003;", "order": "&#9654;", "error": "&#9888;"}
    rows = "".join(
        f'<div class="ev {e["kind"]}"><span class="t">{escape(e["time"].replace("T", " "))}</span>'
        f"<span>{icon.get(e['kind'], '&middot;')}</span>"
        f'<span class="tx">{escape(e["text"])}</span></div>'
        for e in timeline
    )
    title = "Fleet report" if kind == "fleet" else "Robot report"
    version = data.get("version") or "unknown version"
    status_src = "GET /robots + /robots/{id}" if kind == "fleet" else "GET /status"
    trend_src = "GET /order" if kind == "fleet" else "GET /statistics/distance"
    trend_svg = _render_trend(data.get("trend", []), data.get("trend_label", ""))
    timeline_src = "GET /order" if kind == "fleet" else "GET /mission_queue + /log/error_reports"
    timeline_rows = rows or '<p style="color:var(--ink-faint)">nothing yet</p>'
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} — {escape(str(data.get("target", "")))}</title>
<style>{_CSS}</style>
</head>
<body>
<div class="brandbar"></div>
<div class="wrap">
  <h1>{title} <span>{escape(str(version))}</span></h1>
  <div class="sub">{escape(str(data.get("target", "")))} &middot;
  generated from official API endpoints</div>

  <div class="panel">
    <div class="panel-head">Current status <span class="tag">{status_src}</span></div>
    <div class="panel-body"><div class="bots">{"".join(_render_bot(r) for r in robots)}</div></div>
  </div>

  <div class="panel">
    <div class="panel-head">Daily trend — {escape(data.get("trend_label", ""))}
      <span class="tag">{trend_src}</span></div>
    <div class="panel-body">{trend_svg}</div>
  </div>

  <div class="panel">
    <div class="panel-head">Timeline <span class="tag">{timeline_src}</span></div>
    <div class="panel-body"><div class="tl">{timeline_rows}</div></div>
  </div>

  <footer>Generated by <code>mir-report</code> (mir-client) from documented endpoints only —
  works identically against real MiR robots, fleets, and the emulator.</footer>
</div>
</body>
</html>
"""


def write_report(base_url: str, path: str | Path, **kwargs: Any) -> Path:
    """Collect and render in one step; returns the written path."""
    out = Path(path)
    out.write_text(render_report(collect_report(base_url, **kwargs)))
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="mir-report",
        description="Generate a status/trend/timeline dashboard from a MiR robot or fleet.",
    )
    parser.add_argument("target", help="base URL of the robot or fleet (e.g. http://192.168.12.20)")
    parser.add_argument("-o", "--output", default="mir-report.html", help="HTML output path")
    parser.add_argument("--username", default=DEFAULT_USERNAME, help="robot API username")
    parser.add_argument("--password", default=DEFAULT_PASSWORD, help="robot API password")
    parser.add_argument("--api-key", default=DEFAULT_API_KEY, help="fleet x-api-key")
    parser.add_argument("--session", default=None, help="X-MiR-Session id (emulator)")
    parser.add_argument("--json", action="store_true", help="print collected data as JSON too")
    args = parser.parse_args(argv)

    data = collect_report(
        args.target,
        username=args.username,
        password=args.password,
        api_key=args.api_key,
        session_id=args.session,
    )
    out = Path(args.output)
    out.write_text(render_report(data))
    if args.json:
        print(json.dumps(data, indent=1))
    robots = ", ".join(f"{r['name']} {r['battery']}%" for r in data["robots"])
    print(f"mir-report: {data['kind']} {data.get('version')} -> {out} ({robots})")
    return 0


if __name__ == "__main__":
    sys.exit(main())

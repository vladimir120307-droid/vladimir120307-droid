#!/usr/bin/env python3
"""
Aggregate traffic / clones / release downloads / stars across every repo
owned by the authenticated user. Persists daily history (GitHub Traffic API
only keeps 14 days), then renders a stats block into README.md between
<!-- STATS:START --> and <!-- STATS:END --> markers.

Run locally:  GH_TOKEN=<pat> python scripts/update_stats.py
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HISTORY_FILE = ROOT / "data" / "traffic_history.json"
README_FILE = ROOT / "README.md"
USER = os.environ.get("GH_USER", "vladimir120307-droid")
TOKEN = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
START_MARK = "<!-- STATS:START -->"
END_MARK = "<!-- STATS:END -->"
API = "https://api.github.com"

if not TOKEN:
    print("ERROR: GH_TOKEN / GITHUB_TOKEN env var required", file=sys.stderr)
    sys.exit(1)


def api(path: str, params: dict | None = None) -> object:
    url = f"{API}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "profile-stats-bot",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        if e.code in (403, 404):
            return None
        raise


def list_repos() -> list[dict]:
    repos, page = [], 1
    while True:
        chunk = api(
            "/user/repos",
            {"per_page": 100, "page": page, "affiliation": "owner"},
        )
        if not chunk:
            break
        repos.extend(chunk)
        if len(chunk) < 100:
            break
        page += 1
    return [
        r for r in repos
        if not r.get("fork") and not r.get("archived") and not r.get("private")
    ]


def merge_traffic(history: dict, repo_name: str, kind: str, payload: dict | None):
    """Merge fresh 14-day traffic into history (keyed by ISO date)."""
    if not payload:
        return
    bucket = history.setdefault(repo_name, {}).setdefault(kind, {})
    rows_key = "views" if kind == "views" else "clones"
    for row in payload.get(rows_key, []):
        ts = row["timestamp"][:10]
        bucket[ts] = {"count": row.get("count", 0), "uniques": row.get("uniques", 0)}


def sum_kind(history: dict, kind: str, days: int | None = None) -> tuple[int, int]:
    total, uniq = 0, 0
    cutoff = None
    if days is not None:
        import datetime as _dt
        cutoff = (_dt.datetime.now(timezone.utc) - _dt.timedelta(days=days)).date().isoformat()
    for _, kinds in history.items():
        for date, row in kinds.get(kind, {}).items():
            if cutoff and date < cutoff:
                continue
            total += row.get("count", 0)
            uniq += row.get("uniques", 0)
    return total, uniq


def fmt(n: int) -> str:
    if n >= 1_000_000:
        return f"{n/1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n/1_000:.1f}k"
    return str(n)


def shield(label: str, value: str, color: str, logo: str | None = None) -> str:
    # shields.io: '-' splits the segments, so a literal dash must be '--';
    # '_' renders as a space, so a literal underscore must be '__'.
    def esc(s: str) -> str:
        return urllib.parse.quote(s.replace("_", "__").replace("-", "--"), safe="")
    url = (
        f"https://img.shields.io/badge/{esc(label)}-{esc(value)}-{color}"
        f"?style=for-the-badge&labelColor=0d1117"
    )
    if logo:
        url += f"&logo={logo}&logoColor=white"
    return f'<img src="{url}" alt="{label}: {value}" />'


def main() -> None:
    history = {}
    if HISTORY_FILE.exists():
        history = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))

    repos = list_repos()
    print(f"Found {len(repos)} non-fork, non-archived repos owned by {USER}")

    total_stars = 0
    total_forks = 0
    total_release_dl = 0
    per_repo_views_14d: list[tuple[str, int, int]] = []  # name, count, uniques

    for repo in repos:
        owner = repo["owner"]["login"]
        name = repo["name"]
        full = f"{owner}/{name}"
        total_stars += repo.get("stargazers_count", 0)
        total_forks += repo.get("forks_count", 0)

        views = api(f"/repos/{full}/traffic/views")
        clones = api(f"/repos/{full}/traffic/clones")
        merge_traffic(history, full, "views", views)
        merge_traffic(history, full, "clones", clones)

        if views and views.get("views"):
            v_count = sum(r["count"] for r in views["views"])
            v_uniq = sum(r["uniques"] for r in views["views"])
            if v_count:
                per_repo_views_14d.append((full, v_count, v_uniq))

        releases = api(f"/repos/{full}/releases", {"per_page": 100}) or []
        for rel in releases:
            for asset in rel.get("assets", []):
                total_release_dl += asset.get("download_count", 0)

        print(f"  {full}: stars={repo.get('stargazers_count',0)}")

    HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    HISTORY_FILE.write_text(
        json.dumps(history, indent=2, sort_keys=True, ensure_ascii=False),
        encoding="utf-8",
    )

    v_all, vu_all = sum_kind(history, "views")
    c_all, cu_all = sum_kind(history, "clones")
    v_14, vu_14 = sum_kind(history, "views", days=14)
    c_14, cu_14 = sum_kind(history, "clones", days=14)

    per_repo_views_14d.sort(key=lambda r: r[1], reverse=True)
    top5 = per_repo_views_14d[:5]

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    lines = []
    lines.append("")
    lines.append('<div align="center">')
    lines.append("")
    lines.append("### 📡 Live cross-repo telemetry")
    lines.append("")
    lines.append("<p>")
    lines.append("  " + shield("👁️ Views All-time", fmt(v_all), "7c3aed"))
    lines.append("  " + shield("🧬 Unique Visitors", fmt(vu_all), "a855f7"))
    lines.append("  " + shield("📥 Clones All-time", fmt(c_all), "3b82f6"))
    lines.append("  " + shield("👤 Unique Cloners", fmt(cu_all), "0ea5e9"))
    lines.append("</p>")
    lines.append("<p>")
    lines.append("  " + shield("🚀 Release Downloads", fmt(total_release_dl), "10b981"))
    lines.append("  " + shield("⭐ Total Stars", fmt(total_stars), "f59e0b"))
    lines.append("  " + shield("🍴 Total Forks", fmt(total_forks), "ef4444"))
    lines.append("  " + shield("📦 Repositories", str(len(repos)), "8b5cf6"))
    lines.append("</p>")
    lines.append("")
    lines.append("<table>")
    lines.append("<tr>")
    lines.append("<th align='center'>Window</th>")
    lines.append("<th align='center'>👁️ Views</th>")
    lines.append("<th align='center'>🧬 Unique</th>")
    lines.append("<th align='center'>📥 Clones</th>")
    lines.append("<th align='center'>👤 Unique</th>")
    lines.append("</tr>")
    lines.append(
        f"<tr><td align='center'><b>Last 14 days</b></td>"
        f"<td align='center'>{v_14:,}</td><td align='center'>{vu_14:,}</td>"
        f"<td align='center'>{c_14:,}</td><td align='center'>{cu_14:,}</td></tr>"
    )
    lines.append(
        f"<tr><td align='center'><b>All-time*</b></td>"
        f"<td align='center'>{v_all:,}</td><td align='center'>{vu_all:,}</td>"
        f"<td align='center'>{c_all:,}</td><td align='center'>{cu_all:,}</td></tr>"
    )
    lines.append("</table>")
    lines.append("")
    if top5:
        lines.append("<details>")
        lines.append("<summary><b>🔥 Top 5 repos by views (last 14 days)</b></summary>")
        lines.append("")
        lines.append("| # | Repository | Views | Unique |")
        lines.append("|---|---|---:|---:|")
        for i, (full, c, u) in enumerate(top5, 1):
            lines.append(
                f"| {i} | [`{full}`](https://github.com/{full}) | {c:,} | {u:,} |"
            )
        lines.append("")
        lines.append("</details>")
        lines.append("")
    lines.append(
        f"<sub>* All-time counters start from the day this tracker first ran. "
        f"GitHub Traffic API only exposes a rolling 14-day window — earlier numbers "
        f"are accumulated locally in <code>data/traffic_history.json</code>.<br>"
        f"⏱️ Last updated: <b>{now}</b> · refreshed daily via GitHub Actions</sub>"
    )
    lines.append("")
    lines.append("</div>")
    lines.append("")

    block = "\n".join(lines)

    readme = README_FILE.read_text(encoding="utf-8")
    if START_MARK in readme and END_MARK in readme:
        before, _, rest = readme.partition(START_MARK)
        _, _, after = rest.partition(END_MARK)
        new = f"{before}{START_MARK}\n{block}\n{END_MARK}{after}"
    else:
        # First run — append at the end
        new = (
            f"{readme.rstrip()}\n\n---\n\n## 📊 Live Repository Statistics\n\n"
            f"{START_MARK}\n{block}\n{END_MARK}\n"
        )

    if new != readme:
        README_FILE.write_text(new, encoding="utf-8")
        print("README updated")
    else:
        print("README unchanged")


if __name__ == "__main__":
    main()

"""Static status page for GitHub Pages."""

from __future__ import annotations

import html
import shutil
from datetime import datetime, timezone
from pathlib import Path


def build_site(site_dir: Path, text: str, chart: Path | None, account_lines: list[str], log_tail: list[str]) -> Path:
    site_dir.mkdir(parents=True, exist_ok=True)
    image = ""
    if chart is not None and chart.exists():
        shutil.copyfile(chart, site_dir / "chart.png")
        stamp = int(datetime.now(timezone.utc).timestamp())
        image = f'<img src="chart.png?v={stamp}" alt="Gold chart with the current plan levels">'
    account = "\n".join(account_lines)
    recent = "\n".join(reversed(log_tail))
    page = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta http-equiv="refresh" content="300">
  <title>Gold intraday bot</title>
  <style>
    body {{ margin: 0; background: #f4f0e6; color: #1d1a16; font: 15px/1.5 "Segoe UI", system-ui, sans-serif; }}
    main {{ max-width: 920px; margin: 0 auto; padding: 28px 18px 64px; }}
    h1 {{ font-size: 22px; margin: 0 0 4px; }}
    h2 {{ font-size: 16px; margin: 28px 0 8px; }}
    .muted {{ color: #6b6358; margin: 0 0 16px; }}
    img {{ width: 100%; height: auto; border: 1px solid #e4ddd0; }}
    pre {{ white-space: pre-wrap; font: inherit; margin: 0; background: #fbf8f2; border: 1px solid #e4ddd0; padding: 14px; }}
  </style>
</head>
<body>
<main>
  <h1>Gold intraday bot</h1>
  <p class="muted">Paper demo account. Updated every 15 minutes while the gold market is open. This page reloads every 5 minutes.</p>
  {image}
  <h2>Demo account</h2>
  <pre>{html.escape(account)}</pre>
  <h2>Latest brief</h2>
  <pre>{html.escape(text)}</pre>
  <h2>Recent runs</h2>
  <pre>{html.escape(recent)}</pre>
  <p class="muted">Research and paper trading only. Not financial advice.</p>
</main>
</body>
</html>
"""
    (site_dir / "index.html").write_text(page, encoding="utf-8")
    (site_dir / ".nojekyll").write_text("", encoding="utf-8")
    return site_dir / "index.html"

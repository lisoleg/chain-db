"""Generate styled HTML from the detailed Chinese paper markdown."""
import re
import sys

md_path = "docs/ChainDB_Detailed_Paper_CN.md"
html_path = "docs/ChainDB_Detailed_Paper_CN.html"

with open(md_path, "r", encoding="utf-8") as f:
    content = f.read()

# Skip YAML frontmatter
if content.startswith("---"):
    end = content.find("---", 3)
    if end > 0:
        content = content[end + 3:].strip()

lines = content.split("\n")
html_parts = []
in_code = False
in_table = False
skip_next = False

for i, raw_line in enumerate(lines):
    line = raw_line

    # Handle code blocks
    if line.strip().startswith("```"):
        if in_code:
            html_parts.append("</code></pre>")
            in_code = False
        else:
            html_parts.append('<pre><code style="background:transparent;padding:0;color:inherit">')
            in_code = True
        continue

    if in_code:
        safe = line.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        html_parts.append(safe)
        continue

    # Skip math block markers ($$...$$) - just render inline
    # Handle tables
    if "|" in line and line.strip().startswith("|"):
        cells = [c.strip() for c in line.strip().split("|")[1:-1]]
        if cells and all(set(c) <= set("-: ") for c in cells):
            continue  # separator row
        if not in_table:
            html_parts.append('<table style="border-collapse:collapse;width:100%;margin:0.8em 0;font-size:0.92em">')
            in_table = True
        row_html = "<tr>" + "".join(
            f'<td style="padding:0.4em 0.7em;border-bottom:1px solid #e7e5e4">{c}</td>'
            for c in cells
        ) + "</tr>"
        # First row as header
        if not any(html_parts[-1].startswith("<tr") for _ in [1] if html_parts and "<tr>" in (html_parts[-1] if html_parts else "")):
            if "<table" in "".join(html_parts[-3:]) if len(html_parts) >= 3 else "":
                row_html = row_html.replace("<td ", "<th style=\"background:#1e293b;color:white;padding:0.5em 0.7em;text-align:left;font-weight:500\" ")
        html_parts.append(row_html)
        continue
    elif in_table:
        html_parts.append("</table>")
        in_table = False

    # Escape then apply markdown formatting
    safe = line.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    safe = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", safe)
    safe = re.sub(r"\*(.+?)\*", r"<em>\1</em>", safe)
    safe = re.sub(r"`(.+?)`", r"<code>\1</code>", safe)

    # Headers
    if line.startswith("# "):
        html_parts.append(f'<h1 style="font-size:1.8em;color:#1d4ed8;border-bottom:2px solid #1d4ed8;padding-bottom:0.3em;margin-top:1.2em">{safe[2:]}</h1>')
    elif line.startswith("## "):
        html_parts.append(f'<h2 style="font-size:1.4em;border-left:3px solid #1d4ed8;padding-left:0.6em;margin-top:1.5em">{safe[3:]}</h2>')
    elif line.startswith("### "):
        html_parts.append(f'<h3 style="font-size:1.15em;color:#334155;margin-top:1.2em">{safe[4:]}</h3>')
    elif line.startswith("#### "):
        html_parts.append(f'<h4 style="font-size:1.05em;color:#475569">{safe[5:]}</h4>')
    elif line.strip() == "---":
        html_parts.append('<hr style="border:none;border-top:1px solid #e7e5e4;margin:2em 0">')
    elif line.strip().startswith("- "):
        html_parts.append(f'<li>{safe.strip()[2:]}</li>')
    elif re.match(r"^\d+\.\s", line):
        content_m = re.sub(r"^\d+\.\s", "", safe)
        html_parts.append(f'<li>{content_m}</li>')
    elif line.strip() == "":
        html_parts.append("")
    else:
        html_parts.append(safe)

if in_table:
    html_parts.append("</table>")

body_html = "\n".join(html_parts)

full_html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>ChainDB: 面向AGI的关系索引区块链数据库系统 — 设计与实现</title>
<style>
@import url('https://fonts.googleapis.com/css2?family=Noto+Serif+SC:wght@400;600;700&family=Noto+Sans+SC:wght@300;400;500;700&family=JetBrains+Mono:wght@400;500&display=swap');

:root {{
  --bg: #fafaf9;
  --paper: #ffffff;
  --text: #1c1917;
  --text-secondary: #57534e;
  --accent: #1d4ed8;
  --accent-light: #dbeafe;
  --border: #e7e5e4;
  --code-bg: #f5f5f4;
}}

* {{ margin: 0; padding: 0; box-sizing: border-box; }}

body {{
  font-family: 'Noto Sans SC', -apple-system, sans-serif;
  background: var(--bg);
  color: var(--text);
  line-height: 1.8;
  font-size: 15px;
}}

.paper {{
  max-width: 860px;
  margin: 2em auto;
  background: var(--paper);
  box-shadow: 0 1px 3px rgba(0,0,0,0.08), 0 8px 30px rgba(0,0,0,0.06);
  padding: 3em 4em;
}}

pre {{
  background: #1e293b;
  color: #e2e8f0;
  padding: 1.2em 1.5em;
  border-radius: 6px;
  overflow-x: auto;
  margin: 1em 0;
  font-size: 0.85em;
  line-height: 1.6;
  font-family: 'JetBrains Mono', monospace;
}}

code {{
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.88em;
  background: var(--code-bg);
  padding: 0.15em 0.4em;
  border-radius: 3px;
  color: #c2410c;
}}

li {{ margin: 0.3em 0; padding-left: 0.5em; }}

a {{ color: var(--accent); text-decoration: none; }}
a:hover {{ text-decoration: underline; }}

.repo-box {{
  margin: 2em 0;
  padding: 1.2em 1.5em;
  border: 1px solid var(--border);
  border-left: 4px solid var(--accent);
  border-radius: 4px;
  background: #f0f9ff;
}}

.repo-box a {{
  font-family: 'JetBrains Mono', monospace;
  font-weight: 500;
}}

.footer {{
  text-align: center;
  color: var(--text-secondary);
  font-size: 0.85em;
  margin-top: 3em;
  padding-top: 1em;
  border-top: 1px solid var(--border);
}}

@media print {{
  body {{ background: white; }}
  .paper {{ box-shadow: none; margin: 0; padding: 2em; }}
}}

@media (max-width: 768px) {{
  .paper {{ padding: 1.5em; }}
}}
</style>
</head>
<body>
<div class="paper">
<div style="font-family:'Noto Serif SC',serif;font-size:1.15em;color:var(--text-secondary);font-style:italic;margin-bottom:1em">
  信息在关联中，不在实体中
</div>
<div style="color:var(--text-secondary);font-size:0.95em;margin:0.5em 0">
  寇豆码 (lisoleg) · 太乙AGI实验室 · Σ-Cloud团队
</div>
<div style="color:var(--text-secondary);font-size:0.9em;margin-bottom:2em">
  2026年5月 · v3.1
</div>

{body_html}

<div class="repo-box">
<strong>源代码仓库：</strong><a href="https://github.com/lisoleg/chain-db" target="_blank">https://github.com/lisoleg/chain-db</a><br>
94/94 测试全部通过 · 约10,600行代码 · 57个文件
</div>

<div class="footer">
  ChainDB v3.1 — 面向AGI的关系索引区块链数据库系统 — 设计与实现
</div>
</div>
</body>
</html>
"""

with open(html_path, "w", encoding="utf-8") as f:
    f.write(full_html)

print(f"HTML generated: {html_path}")
print(f"Markdown: {md_path}")

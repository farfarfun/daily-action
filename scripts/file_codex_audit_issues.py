"""为 run_codex_audit.py 的发现分别建 issue，按仓库聚合、按置信度过滤、去重。

这条流水线跑在 farfarfun/daily-action，但 issue 集中建在 farfarfun/todo-list
（沿用 todo-list 现有 file_py_typed_issues.py 等审计脚本的惯例，REPO 常量硬编码
指向 todo-list，不是本仓库），所以调 gh 的 GH_TOKEN 必须有跨仓库权限。

dedup 是硬约束（用户明确要求"避免重复新建"）：建之前搜一遍 todo-list 里该仓库是否
已有 open 的 codex-audit 标签 issue，命中就跳过。codex_audit_cursor.py 选批次时
已经排掉过一次同类仓库，这里是最后一道防线，防止同一批次内部重复、或人工用
workflow_dispatch 重跑时产生重复。

只有 medium/high 置信度的发现才建 issue——low 置信度大概率是模型没把握的猜测，
不单独占用 issue 噪音仓库维护者。
"""

import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = "farfarfun/todo-list"
FINDINGS_PATH = os.path.join(HERE, "codex_audit_findings.json")
CREATED_PATH = os.path.join(HERE, "codex_audit_issues.json")
TITLE_PREFIX = "[codex审计]"
MIN_CONFIDENCE = ("medium", "high")


def gh(args, input_text=None):
    r = subprocess.run(["gh", *args], capture_output=True, text=True, input=input_text)
    if r.returncode != 0:
        print(f"  !! 失败: {' '.join(args)}\n{r.stderr}", file=sys.stderr)
        return None
    return r.stdout


def has_open_issue(repo_name):
    out = gh([
        "issue", "list", "--repo", REPO, "--label", "codex-audit",
        "--state", "open", "--limit", "500", "--json", "title",
    ])
    if not out:
        return False
    prefix = f"{TITLE_PREFIX} {repo_name}:"
    return any(row["title"].startswith(prefix) for row in json.loads(out))


def build_body(repo_name, findings, skipped_low):
    lines = [
        "⚠️ 本 issue 由 Codex CLI 自动生成（org 级合规审计流水线，模型 "
        "`gpt-5.6-luna`），**不是**人工核对过的发现，可能存在误判。任何自动/"
        "人工修复合并前请先核实下面的证据是否站得住。",
        "",
        f"对照 [SPEC.md](https://github.com/farfarfun/todo-list/blob/master/SPEC.md) "
        f"检查 `{repo_name}` 发现以下问题：",
        "",
    ]
    for i, f in enumerate(findings, 1):
        lines += [
            f"### {i}. {f['rule']}（{f['spec_section']}，置信度：{f['confidence']}）",
            "",
            f"**证据**：{f['evidence']}",
            "",
            f"**建议修复**：{f['suggested_fix']}",
            "",
        ]
    if skipped_low:
        lines.append(
            f"另有 {skipped_low} 条 low 置信度的发现未列出（模型没把握，不单独占位）。"
        )
    return "\n".join(lines)


def main():
    results = json.load(open(FINDINGS_PATH, encoding="utf-8"))
    created = []
    skipped_dup = []

    for row in results:
        repo_name = row["repo"]
        all_findings = row.get("findings", [])
        findings = [f for f in all_findings if f.get("confidence") in MIN_CONFIDENCE]
        skipped_low = len(all_findings) - len(findings)
        if not findings:
            continue
        if has_open_issue(repo_name):
            print(f"  跳过 {repo_name}：已有 open 的 codex-audit issue")
            skipped_dup.append(repo_name)
            continue

        title = f"{TITLE_PREFIX} {repo_name}: {len(findings)} 条规范违规待核实"
        body = build_body(repo_name, findings, skipped_low)
        out = gh([
            "issue", "create", "--repo", REPO, "--title", title,
            "--body", body, "--label", "codex-audit",
        ])
        if out:
            url = out.strip()
            created.append({"repo": repo_name, "url": url})
            print(f"  {repo_name} -> {url}")
        else:
            print(f"  {repo_name} -> 建 issue 失败")

    json.dump(created, open(CREATED_PATH, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"\n完成，新建 {len(created)} 条 issue，因已存在跳过 {len(skipped_dup)} 个仓库")


if __name__ == "__main__":
    main()

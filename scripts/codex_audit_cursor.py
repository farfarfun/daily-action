"""计算 codex-find-issues workflow 本轮要扫描的仓库批次。

从 todo-list 的 mapping.json 取全量仓库列表（跳过归档仓库），用本仓库
codex_audit_cursor.json 里的游标滚动推进；同时跳过已经有 open 的 codex-audit
标签 issue 的仓库（issue 建在 farfarfun/todo-list，不是本仓库）——这一层
既避免重复扫描浪费 API 调用，也是"避免重复建 issue"的第一道防线（第二道在
file_codex_audit_issues.py 建 issue 前的即时检查）。

这个流水线本身跑在 farfarfun/daily-action（找问题+改代码的自动化归 daily-action
管），但 issue 集中建在 farfarfun/todo-list（沿用现有审计角度的惯例），mapping.json
和 SPEC.md 也只存在于 todo-list，所以本脚本从 TODO_LIST_DIR 指向的一份 todo-list
克隆里读取它们，而不是本仓库自己的路径。

批次列表写到 codex_audit_batch.json，游标写回 codex_audit_cursor.json（都留在
本仓库，因为游标状态是这条流水线自己的运行状态，与 todo-list 内容无关）。
"""

import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = "farfarfun/todo-list"
TODO_LIST_DIR = os.environ.get("TODO_LIST_DIR", os.path.join(HERE, "..", "todo-list-ref"))
MAPPING_PATH = os.path.join(TODO_LIST_DIR, "scripts", "mapping.json")
CURSOR_PATH = os.path.join(HERE, "codex_audit_cursor.json")
BATCH_PATH = os.path.join(HERE, "codex_audit_batch.json")
TITLE_PREFIX = "[codex审计]"


def gh(args):
    r = subprocess.run(["gh", *args], capture_output=True, text=True)
    if r.returncode != 0:
        print(f"  !! 失败: {' '.join(args)}\n{r.stderr}", file=sys.stderr)
        return None
    return r.stdout


def repos_with_open_issue():
    out = gh([
        "issue", "list", "--repo", REPO, "--label", "codex-audit",
        "--state", "open", "--limit", "500", "--json", "title",
    ])
    if not out:
        return set()
    names = set()
    for row in json.loads(out):
        title = row["title"]
        if title.startswith(f"{TITLE_PREFIX} "):
            rest = title[len(f"{TITLE_PREFIX} "):]
            names.add(rest.split(":", 1)[0].strip())
    return names


def main():
    repos = json.load(open(MAPPING_PATH, encoding="utf-8"))
    names = [r["repo"] for r in repos if not r.get("archived")]
    if not names:
        print("mapping.json 里没有可扫描的仓库", file=sys.stderr)
        sys.exit(1)

    cursor = json.load(open(CURSOR_PATH, encoding="utf-8"))
    batch_size = cursor.get("batch_size", 15)
    start = (cursor.get("last_index", -1) + 1) % len(names)

    skip = repos_with_open_issue()
    print(f"跳过 {len(skip)} 个已有 open codex-audit issue 的仓库")

    batch = []
    idx = start
    seen = 0
    last_idx = cursor.get("last_index", -1)
    while len(batch) < batch_size and seen < len(names):
        name = names[idx]
        if name not in skip:
            batch.append(name)
        last_idx = idx
        idx = (idx + 1) % len(names)
        seen += 1

    cursor["last_index"] = last_idx
    json.dump(cursor, open(CURSOR_PATH, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    json.dump(batch, open(BATCH_PATH, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    print(f"本轮批次（{len(batch)}/{batch_size} 个，游标推进到 {last_idx}）：{batch}")


if __name__ == "__main__":
    main()

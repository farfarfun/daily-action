"""codex-find-issues workflow 第三步：逐仓库跑 Codex CLI，对照 SPEC.md 找规范违规。

只读扫描：本来用 `--sandbox read-only`，但 codex exec 的 bwrap 沙箱在 GitHub
Actions 的 Ubuntu 24.04 runner 上因 AppArmor 限制非特权 user namespace 而直接
启动失败（https://github.com/openai/codex/issues/15957），报错会被 codex 当成
一条"high 置信度"的假发现写进输出。改用 `--sandbox danger-full-access` 跳过
bwrap，只读约束改成 clone 完之后手动 chmod 整个仓库目录为不可写（见
`clone_repo` 里的 chmod 调用）兜底，不依赖 codex 自己的沙箱。

模型固定 gpt-5.6-luna（用户指定的最便宜档位，全组织 144+ 仓库的扫描成本才扛得住）
——这不是标准 OpenAI 模型名，说明走的是自定义/代理的 OpenAI 兼容端点，所以还需要
能覆盖 base_url（见 setup_codex_home）。

这条流水线跑在 farfarfun/daily-action（找问题/改代码的自动化归这个仓库管），但
SPEC.md 只存在于 farfarfun/todo-list，所以从 TODO_LIST_DIR 指向的一份 todo-list
克隆里读取，而不是本仓库自己的路径。

每个仓库的发现按 codex_audit_schema.json 的结构输出，汇总写入
codex_audit_findings.json，供 file_codex_audit_issues.py 去重后建 issue
（issue 集中建在 farfarfun/todo-list，沿用现有审计角度的惯例）。

克隆用 ORG_PAT（而不是默认 GITHUB_TOKEN），因为组织里有私有仓库，默认 token
的权限范围只到 workflow 所在的 daily-action 自己。
"""

import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ORG = "farfarfun"
TODO_LIST_DIR = os.environ.get("TODO_LIST_DIR", os.path.join(HERE, "..", "todo-list-ref"))
SPEC_PATH = os.path.join(TODO_LIST_DIR, "SPEC.md")
SCHEMA_PATH = os.path.join(HERE, "codex_audit_schema.json")
BATCH_PATH = os.path.join(HERE, "codex_audit_batch.json")
FINDINGS_PATH = os.path.join(HERE, "codex_audit_findings.json")
MODEL = "gpt-5.6-luna"
CODEX_TIMEOUT_SEC = 900

# codex.toml 里覆盖内置 "openai" provider 的 base_url 有已知 bug（不生效），
# 所以走"自定义 provider + 设为默认"这条路，而不是直接改 openai_base_url。
CODEX_CONFIG_TEMPLATE = """model_provider = "custom"

[model_providers.custom]
name = "custom"
base_url = {base_url!r}
wire_api = "responses"
env_key = "CODEX_API_KEY"
requires_openai_auth = false
"""


def setup_codex_home(base_url):
    """base_url 非空时，生成一个独立 CODEX_HOME，写入指向自定义端点的 config.toml。

    返回值是要注入 codex 子进程 env 的 CODEX_HOME 路径；base_url 为空则返回 None，
    codex 用默认的 ~/.codex（走官方 OpenAI 端点）。
    """
    if not base_url:
        return None
    codex_home = tempfile.mkdtemp(prefix="codex-home-")
    config_path = os.path.join(codex_home, "config.toml")
    with open(config_path, "w", encoding="utf-8") as f:
        f.write(CODEX_CONFIG_TEMPLATE.format(base_url=base_url))
    return codex_home

PROMPT_TEMPLATE = """你在对 GitHub 组织 farfarfun 里的仓库 {repo} 做一次只读的开发规范合规审计。

下面是本组织的开发规范全文（SPEC.md），请只依据这份规范判断，不要用你自己的通用最佳实践标准：

<SPEC.md>
{spec}
</SPEC.md>

仓库代码已经克隆到当前工作目录，你可以自由读取文件内容——这是只读审计，不要修改任何文件。

请重点检查脚本类审计工具覆盖不到的部分：README 结构与内容质量、代码风格与命名、
日志与错误处理写法、依赖管理是否合理、配置与凭据处理方式、文档与测试的实质内容质量等。
已经有专门脚本在查的机械性问题（py.typed 标记、build-backend 声明、LICENSE 文件存在与否等）
如果顺手发现也可以报，但不是重点。

对每一条发现给出：违反的规则、对应 SPEC.md 章节、证据（具体文件路径 + 内容片段）、
置信度（low/medium/high——没把握就标 low，不要为了凑数量硬报）、建议的修复方式。
如果整体符合规范，findings 留空数组，不要为了有输出硬找问题。

最终只输出符合给定 schema 的 JSON，不要输出其它文字。"""


def gh(args):
    r = subprocess.run(["gh", *args], capture_output=True, text=True)
    if r.returncode != 0:
        print(f"  !! 失败: {' '.join(args)}\n{r.stderr}", file=sys.stderr)
        return None
    return r.stdout


def clone_repo(repo_name, dest, token):
    url = f"https://x-access-token:{token}@github.com/{ORG}/{repo_name}.git"
    r = subprocess.run(
        ["git", "clone", "--depth", "1", url, dest],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        # stderr 里可能带着 clone URL（含 token），逐行过滤掉包含 token 的行再打印
        safe_err = "\n".join(
            line for line in r.stderr.splitlines() if token not in line
        )
        print(f"  !! clone 失败 ({repo_name}): {safe_err[-500:]}", file=sys.stderr)
        return False
    # codex exec 用 --sandbox danger-full-access（bwrap 在 CI 里起不来），
    # 只读约束靠这里手动 chmod 整个目录树为不可写来兜底
    subprocess.run(["chmod", "-R", "a-w", dest], check=False)
    return True


def run_codex(repo_path, repo_name, spec_text, base_env):
    prompt = PROMPT_TEMPLATE.format(repo=repo_name, spec=spec_text)
    fd, out_path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    env = dict(base_env)
    cmd = [
        "codex", "exec",
        "--sandbox", "danger-full-access",
        "--skip-git-repo-check",
        "-m", MODEL,
        "--output-schema", SCHEMA_PATH,
        "-o", out_path,
        prompt,
    ]
    try:
        r = subprocess.run(
            cmd, cwd=repo_path, capture_output=True, text=True,
            env=env, timeout=CODEX_TIMEOUT_SEC,
        )
    except subprocess.TimeoutExpired:
        print(f"  !! codex 超时 ({repo_name})", file=sys.stderr)
        os.unlink(out_path) if os.path.exists(out_path) else None
        return None

    if r.returncode != 0:
        print(f"  !! codex 执行失败 ({repo_name}): {r.stderr[-2000:]}", file=sys.stderr)
        return None
    try:
        with open(out_path, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"  !! 解析 codex 输出失败 ({repo_name}): {e}", file=sys.stderr)
        return None
    finally:
        if os.path.exists(out_path):
            os.unlink(out_path)


def main():
    batch = json.load(open(BATCH_PATH, encoding="utf-8"))
    spec_text = open(SPEC_PATH, encoding="utf-8").read()
    token = os.environ.get("ORG_PAT")
    if not token:
        print("缺少 ORG_PAT 环境变量，无法克隆仓库", file=sys.stderr)
        sys.exit(1)

    base_env = dict(os.environ)
    if "OPENAI_API_KEY" in base_env:
        base_env["CODEX_API_KEY"] = base_env["OPENAI_API_KEY"]
    base_url = os.environ.get("OPENAI_BASE_URL")
    codex_home = setup_codex_home(base_url)
    if codex_home:
        base_env["CODEX_HOME"] = codex_home
        print(f"使用自定义 OpenAI base_url: {base_url}")

    results = []
    with tempfile.TemporaryDirectory() as tmp:
        for i, repo_name in enumerate(batch, 1):
            print(f"[{i}/{len(batch)}] {repo_name}")
            dest = os.path.join(tmp, repo_name)
            if not clone_repo(repo_name, dest, token):
                continue
            finding = run_codex(dest, repo_name, spec_text, base_env)
            if finding is None:
                continue
            finding["repo"] = repo_name
            results.append(finding)
            n = len(finding.get("findings", []))
            print(f"  -> {n} 条发现")

    json.dump(results, open(FINDINGS_PATH, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    n_findings = sum(len(r.get("findings", [])) for r in results)
    print(f"\n完成，扫描 {len(results)}/{len(batch)} 个仓库，共 {n_findings} 条发现")


if __name__ == "__main__":
    main()

# daily-action

farfarfun 组织的定时任务集合，参考 [farfarfun-skills/daily-action](https://github.com/farfarfun-skills/daily-action)。

## Mirror to Gitee

`Mirror to Gitee` 每天 00:10（Asia/Shanghai）运行一次，也可手动触发（`workflow_dispatch`）。
它会把 `farfarfun` 组织下所有**公开**仓库（含 fork）镜像同步到 Gitee 的 `farfarfun` 组织，
不会同步私有仓库。

需要在本仓库的 Actions secrets 中配置：

- `GITEE_TOKEN`：Gitee 私人令牌，需要有 `projects` 权限，用于在 `farfarfun` 组织下创建/更新仓库。
- `GITEE_RSA_PRIVATE_KEY`：用于推送代码的 SSH 私钥，对应公钥需添加到该 Gitee 账号的 SSH 公钥中。

配置好后，Gitee 侧需已存在 `farfarfun` 组织（个人无法在他人组织下自动建仓）。

# tbqrfly

Flutter 项目打包、Zealot 上传与飞书二维码发布小工具。

## 安装

```bash
curl -fsSL https://raw.githubusercontent.com/xSILENCEx/tbqrfly/main/install.sh | bash
```

本地开发安装：

```bash
python3 -m pip install -e .
# 或
bash install.sh
```

更新：

```bash
tbqrfly update
```

需要 Python ≥ 3.9。

## 快速开始

在 Flutter 项目根目录：

```bash
tbqrfly init
# 按提示填写 .tbqrfly/zealot.json 与 builds.json 中的 channel
tbqrfly doctor
tbqrfly run
```

非交互：

```bash
tbqrfly run --env dev --platform android
tbqrfly run --env prod --platform all
```

## 配置目录

`tbqrfly init` 会创建（并加入 `.gitignore`）：

```text
.tbqrfly/
├── zealot.json
├── feishu.json
├── message.md
├── llm.json
├── prompt.md
├── builds.json
└── commit_history.json
```

### 消息占位符

`message.md` 支持：`{platform}` `{version}` `{buildNumber}` `{commit}` `{env}` `{app_name}` `{summary}`。

`{platform}` 展示为 `Android` / `iOS`。

### 运行流程

1. 选择环境 / 平台（可超时默认）
2. 共享阶段：读取项目信息 + LLM 摘要（失败则回退 git log）
3. 多平台时：当前终端优先跑 iOS，其余平台并行
4. 各平台：产物复用询问 → 构建（追加 `GIT_COMMIT`）→ Zealot 上传 → 飞书二维码

`builds.json` 的 `terminal`：

| 值 | 行为 |
|----|------|
| `auto`（默认） | 在 Cursor/VS Code 等 IDE 内用 **inline**；否则新开系统 Terminal |
| `inline` | 始终在当前终端并行（日志带 `[android]` / `[ios]` 前缀） |
| `system` | 始终新开 macOS Terminal.app |

> Cursor/VS Code **没有**稳定的公开 API 去「新开一个集成终端面板」跑命令，因此 IDE 场景用 inline 并行，而不是系统终端。
>
> 同仓库并行 `flutter build` 为尽力而为，可能偶发争用。

## 命令

| 命令 | 说明 |
|------|------|
| `tbqrfly init` | 初始化配置；致命环境问题会中止 |
| `tbqrfly doctor` | 环境与可选 LLM 检查 |
| `tbqrfly run` | 发布流程 |
| `tbqrfly update` | 从 GitHub 更新工具 |

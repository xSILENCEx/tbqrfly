# tbqrfly 使用教程

本教程按「先基础设施、再装工具、再配项目、最后发布」的顺序说明如何用 **tbqrfly** 完成 Flutter 打包、上传到 **Zealot**，并把下载二维码发到 **飞书**。

## 你会得到什么

执行 `tbqrfly run` 后，工具会：

1. 选择环境 / 平台  
2. （可选）执行前置命令，如 `git pull`、`flutter pub get`  
3. 生成变更摘要（LLM，失败则回退 git log）  
4. 构建 APK / IPA  
5. 上传到 Zealot，拿到安装页与二维码  
6. 将二维码图片发到飞书群  

---

## 0. 前置条件总览

| 项 | 是否必须 | 说明 |
|----|----------|------|
| Python ≥ 3.9 | 是 | 运行 tbqrfly |
| Flutter 项目 + `flutter` / `git` | 是 | 本地打包 |
| **已部署的 Zealot** | 是 | 分发与二维码 |
| 飞书应用 + 群机器人 | 建议 | 发二维码通知；不配则只上传不通知 |
| Claude / 其它 LLM CLI | 否 | 写变更说明；没有则用 git log |

---

## 1. 提前部署 Zealot（必须）

tbqrfly **不会**替你搭建分发平台，需要你先有一台可用的 Zealot。

- 开源仓库：https://github.com/tryzealot/zealot  
- 官方文档：https://zealot.ews.im/  
- **推荐用 Docker 部署**：https://github.com/tryzealot/zealot-docker  

### 1.1 为什么要先部署

- 上传产物、生成安装链接 / 二维码都依赖 Zealot HTTP API  
- iOS 真机安装一般需要 **公网 HTTPS**（Let's Encrypt 等）  
- 自签证书要在每台 iOS 设备上手动信任，不推荐  

### 1.2 Docker 一键部署（推荐）

环境要求：Git 2.0+、Docker 20.10+、Docker Compose 1.28+。

```bash
git clone https://github.com/tryzealot/zealot-docker.git
cd zealot-docker
```

按官方说明配置环境变量（参考仓库内 `example.env`），至少确认：

- 访问域名（如 `zealot.example.com`）  
- 证书方式：Let's Encrypt / 反代终止 SSL /（不推荐）自签  

然后执行部署脚本并启动（以仓库当前脚本名为准，常见为 `./deploy` 或文档中的步骤）：

```bash
./deploy
# 检查并编辑生成的 .env
docker compose up -d
# 若文档要求首次初始化数据库，按提示执行，例如：
# docker compose run --rm zealot run_upgrade
```

更细的步骤见：

- https://zealot.ews.im/docs/self-hosted/deployment/docker  
- https://zealot.ews.im/docs/self-hosted/deployment/docker/step-by-step  

默认管理员账号以官方文档 / 部署脚本说明为准（历史上常见示例为管理员邮箱与初始密码，**部署后请立刻修改**）。

### 1.3 在 Zealot 里准备应用渠道

登录 Zealot 后台后：

1. 创建应用（或使用已有应用）  
2. 为不同环境 / 平台建好 **渠道（Channel）**，例如：  
   - Android Dev / iOS Dev  
   - Android Prod / iOS Prod  
3. 打开渠道详情，复制 **Channel Key**（即 tbqrfly 里的 `zealot_channel_key`）  

### 1.4 获取 Zealot API Token

1. 登录 Zealot  
2. 打开当前用户详情 / 个人设置  
3. 找到 **API Key / Token**（文档说明在用户详情页底部的 API Key）  
4. 复制保存，填入项目 `.tbqrfly/zealot.json` 的 `token`  

上传接口形态（tbqrfly 内部已封装）：

```http
POST {ZEALOT_BASE_URL}/api/apps/upload
```

需要：`token`、`channel_key`、安装包文件等。详见：https://zealot.ews.im/docs/developer-guide/api  

### 1.5 填到 tbqrfly

`.tbqrfly/zealot.json`：

```json
{
  "base_url": "https://zealot.example.com",
  "token": "你的_API_Token"
}
```

`.tbqrfly/builds.json` 各环境平台：

```json
"android": {
  "zealot_channel_key": "渠道 Key",
  "build_command": "flutter build apk --release --split-per-abi"
}
```

---

## 2. 获取飞书相关信息（建议）

不配置飞书时，tbqrfly 仍可构建并上传 Zealot，只是**不会**发群消息。

飞书通知需要三类信息：

| 配置项 | 用途 |
|--------|------|
| `webhook_url` | 往指定群发消息 |
| `app_id` / `app_secret` | 调用开放平台上传二维码图片 |

### 2.1 创建飞书企业自建应用（拿 app_id / app_secret）

1. 打开 [飞书开放平台](https://open.feishu.cn/)（国际版为 Lark 对应控制台）  
2. 创建 **企业自建应用**  
3. 在「凭证与基础信息」中复制：  
   - **App ID** → `feishu.json` 的 `app_id`  
   - **App Secret** → `feishu.json` 的 `app_secret`  
4. 开通权限（以控制台实际权限名为准，通常需要）：  
   - 获取 `tenant_access_token`  
   - **上传图片**（用于把二维码图传到飞书，拿到 `image_key`）  
5. 发布应用版本，并确保对你们租户可用  

> tbqrfly 会用 `app_id` + `app_secret` 换 `tenant_access_token`，再调用图片上传接口，最后通过 webhook 发带图的 post 消息。

### 2.2 添加群自定义机器人（拿 webhook_url）

1. 打开目标飞书群 → 设置 → **群机器人** → 添加 **自定义机器人**  
2. 设置名称后创建，复制 **Webhook 地址**  
3. 填入 `feishu.json` 的 `webhook_url`  

注意：

- Webhook 相当于群的写入凭证，**不要提交到 Git**（`.tbqrfly/` 默认已 gitignore）  
- 若机器人开启了签名校验等高级安全设置，需与实现匹配；当前 tbqrfly 按普通 webhook POST 发送  

### 2.3 填到 tbqrfly

`.tbqrfly/feishu.json`：

```json
{
  "webhook_url": "https://open.feishu.cn/open-apis/bot/v2/hook/xxxxxxxx",
  "app_id": "cli_xxxxxxxx",
  "app_secret": "xxxxxxxx",
  "message_template": "message.md"
}
```

消息正文在 `message.md`，支持占位符：

| 占位符 | 含义 |
|--------|------|
| `{app_name}` | 应用名 |
| `{platform}` | `Android` / `iOS` |
| `{version}` | 版本名 |
| `{buildNumber}` | 构建号 |
| `{commit}` | 短 commit |
| `{env}` | 环境名，如 `dev` |
| `{summary}` | 变更摘要 |

失败时会另发文本消息（含失败平台与错误原因），不依赖二维码图片。

---

## 3. 安装 tbqrfly

```bash
curl -fsSL https://raw.githubusercontent.com/xSILENCEx/tbqrfly/main/install.sh | bash
```

需要 Python ≥ 3.9。安装后确认：

```bash
tbqrfly --version
tbqrfly doctor
```

升级：

```bash
tbqrfly update
```

开发者本地：

```bash
git clone https://github.com/xSILENCEx/tbqrfly.git
cd tbqrfly
python3 -m pip install -e .
```

---

## 4. 在 Flutter 项目里初始化

在项目根目录（有 `pubspec.yaml`）：

```bash
tbqrfly init
```

会：

- 创建 `.tbqrfly/` 并写入默认配置（整目录加入 `.gitignore`）  
- 尝试从 Xcode `DEVELOPMENT_TEAM` 生成 `ExportOptions.plist`  
- 在 `.vscode/tasks.json` 追加任务 **`tbqrfly: run`**（没有则创建）  

已有配置只需补 ExportOptions 时：

```bash
tbqrfly init --repair-export
```

### 4.1 目录说明

```text
.tbqrfly/
├── zealot.json          # Zealot 地址与 Token
├── feishu.json          # 飞书凭证与消息模板文件名
├── llm.json             # LLM 开关与命令
├── prompt.md            # LLM 提示词
├── message.md           # 飞书成功消息模板
├── builds.json          # 环境 / 平台 / 构建 / 渠道 / 前置命令
├── ExportOptions.plist  # iOS 导出（若已生成）
└── commit_history.json  # 摘要缓存（工具维护）
```

### 4.2 必填检查清单

1. `zealot.json`：`base_url`、`token`  
2. `builds.json`：每个要用的 `zealot_channel_key`；按需改 `build_command`  
3. iOS：`export_options` 指向有效 plist  
4. （建议）`feishu.json` 三项凭证  

### 4.3 builds.json 常用字段

```json
{
  "app_name": "MyApp",
  "default_env": "dev",
  "wait_timeout": 10,
  "terminal": "auto",
  "pre_commands": [
    "git pull",
    "flutter pub get"
  ],
  "artifacts": {
    "android": ["build/app/outputs/flutter-apk/app-arm64-v8a-*-release.apk"],
    "ios": ["build/ios/ipa/*.ipa"]
  },
  "builds": {
    "dev": {
      "android": {
        "zealot_channel_key": "...",
        "build_command": "flutter build apk --release --split-per-abi"
      },
      "ios": {
        "zealot_channel_key": "...",
        "build_command": "flutter build ipa --release",
        "export_options": ".tbqrfly/ExportOptions.plist"
      }
    }
  }
}
```

| 字段 | 说明 |
|------|------|
| `pre_commands` | 选完环境/平台后、打包前执行；`[]` 则跳过 |
| `terminal` | `auto`：IDE 内 inline 并行；`system`：系统 Terminal；`inline`：当前终端并行 |
| `build_command` | 完整构建命令；需要 flavor 时自行追加 `--flavor xxx` |

构建时工具会追加 `--dart-define=GIT_COMMIT=短提交号`，不改写你命令的主体。

---

## 5. （可选）LLM 变更摘要

`.tbqrfly/llm.json`：

```json
{
  "enabled": true,
  "command": "claude -p",
  "prompt": "prompt.md"
}
```

- `init` **不检查** LLM 是否安装  
- `run` 时若启用但命令不可用 / 失败，会回退为 git 提交列表，不阻断发布  
- 提示词在 `prompt.md`，可用 `{git_log}`、`{range_text}` 等占位符  

---

## 6. 日常发布

```bash
tbqrfly doctor   # 建议先自检
tbqrfly run
```

或 VS Code / Cursor：**Terminal → Run Task → `tbqrfly: run`**

非交互：

```bash
tbqrfly run --env dev --platform android
tbqrfly run --env prod --platform all
```

### 6.1 流程摘要

1. 选环境 / 平台（超时用 `default_env` / `all`）  
2. 执行 `pre_commands`  
3. 生成摘要  
4. 多平台：当前终端优先 iOS，其它平台按 `terminal` 并行  
5. 每平台：询问是否复用已有产物 → 构建 → 上传 Zealot → 飞书发二维码  
6. 任一步失败：尝试发飞书失败文本，并以非零退出码结束  

### 6.2 协作说明

- `.tbqrfly/` 默认不进 Git；同事需各自 `init` 或复制配置  
- 不要把 Zealot Token、飞书 Secret、Webhook 提交到仓库  

---

## 7. 常见问题

**Q: iOS 装不上 / 需要 HTTPS？**  
A: Zealot 请用可信 HTTPS；自签证书要在设备上安装并信任。

**Q: `export_options` 为空？**  
A: 先在 Xcode 给 Runner 配好 Team，再执行 `tbqrfly init --repair-export`。

**Q: 只上传不发飞书？**  
A: 留空或不配齐 `feishu.json` 即可；工具会跳过通知。

**Q: 为什么生成过 `.shared_*.json`？**  
A: 多平台分叉用的临时状态；0.1.4+ 会自动清理，也可手动删。

**Q: IDE 里为什么不弹系统终端？**  
A: `terminal: auto` 在 Cursor/VS Code 内默认 inline 并行；要系统终端可设 `"terminal": "system"`。

---

## 8. 参考链接

| 资源 | 链接 |
|------|------|
| tbqrfly | https://github.com/xSILENCEx/tbqrfly |
| Zealot 源码 | https://github.com/tryzealot/zealot |
| Zealot Docker 部署 | https://github.com/tryzealot/zealot-docker |
| Zealot 文档 | https://zealot.ews.im/ |
| Zealot API | https://zealot.ews.im/docs/developer-guide/api |
| 飞书开放平台 | https://open.feishu.cn/ |

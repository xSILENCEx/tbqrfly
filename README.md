# tbqrfly

Flutter 项目打包、上传 [Zealot](https://github.com/tryzealot/zealot)、并把安装二维码发到飞书的小工具。

## 完整教程

**请先阅读：** [docs/tutorial.md](docs/tutorial.md)

教程覆盖：

1. **提前部署 Zealot**（开源仓库与 Docker 部署）  
2. **获取 Zealot Token / Channel Key**  
3. **获取飞书 app_id、app_secret、群 Webhook**  
4. 安装 tbqrfly、项目 `init`、配置与日常 `run`  

## 一分钟上手（已有 Zealot + 飞书时）

```bash
# 安装（Python ≥ 3.9）
curl -fsSL https://raw.githubusercontent.com/xSILENCEx/tbqrfly/main/install.sh | bash

# 在 Flutter 项目根目录
tbqrfly init
# 填写 .tbqrfly/zealot.json、builds.json 渠道，以及（可选）feishu.json
tbqrfly doctor
tbqrfly run
```

升级：

```bash
tbqrfly update
```

## 命令

| 命令 | 说明 |
|------|------|
| `tbqrfly init` | 初始化 `.tbqrfly/`，并写入 VS Code 任务 `tbqrfly: run` |
| `tbqrfly init --repair-export` | 按 Xcode Team 生成/回填 ExportOptions |
| `tbqrfly doctor` | 环境检查 |
| `tbqrfly run` | 发布 |
| `tbqrfly update` | 升级工具 |

## 相关链接

- Zealot：https://github.com/tryzealot/zealot  
- Zealot Docker：https://github.com/tryzealot/zealot-docker  
- Zealot 文档：https://zealot.ews.im/  

## 自定义前后置命令

`.tbqrfly/builds.json` 的顶层和 `builds.<env>.<platform>` 均支持
`pre_commands` / `next_commands` 字符串数组（可省略，默认 `[]`）。
执行顺序为：全局前置一次 → 各平台并行执行前置、发布、后置 → 全部成功后全局后置一次。
`next_commands` 仅成功后执行；命令失败会使对应任务失败。
详见 [命令配置与执行顺序](docs/tutorial.md#44-前后置命令的顺序与失败行为)。

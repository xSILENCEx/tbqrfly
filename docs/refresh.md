# 保留自定义内容刷新配置

升级工具后，用 `refresh` 补齐配置，不需要重新执行 `init --force`：

```bash
tbqrfly update
tbqrfly refresh --dry-run
tbqrfly refresh
```

支持 `--project /path/to/flutter/project`。刷新不执行构建、命令钩子或网络请求。

## 补齐规则

- 以当前工具模板为默认值，只补缺失的字段；已有值始终保留，包括凭据、自定义命令、环境变量占位符、空字符串、`false`、`null` 和空数组。
- 数组整体保留，不追加默认命令或产物路径，不覆盖旧值来跟随模板默认值变化。
- `builds.json` 只补已有环境和平台内的字段；自定义环境同样支持，不重新添加已移除的 dev/prod 或平台。
- `beta.json` 同样只补已有平台，不重新添加已移除的 Android 示例。
- 自定义发布器保留原配置，不向其中加入其它发布器的凭据字段。
- 自定义字段和文件保留；不删除、改名、移动字段，也不进行结构迁移。
- 工具已经兼容的旧凭据字段保持原样，避免新增同义空字段遮蔽旧值。
- 已有 `message.md` 和 `prompt.md` 不改写；需要的默认模板文件缺失时才新增。不改写 plist、私钥、提交记录、运行时目录或 `.vscode/tasks.json`。
- 配置结构不兼容或 JSON 无效时，在任何配置写入前停止，不猜测转换方式。

发生补齐的 JSON 文件会重新缩进输出；键值保持上述规则，原始文件字节保存在备份中。
请避免在刷新过程中同时编辑配置。

## 刷新范围

默认刷新已经初始化的配置：

| 命令 | 范围 |
| --- | --- |
| `tbqrfly refresh` | 检测已有 builds.json / beta.json，刷新对应配置和共享文件 |
| `tbqrfly refresh --target run` | Zealot 发布配置；缺失的配置文件按模板补齐 |
| `tbqrfly refresh --target beta` | Beta 发布配置；缺失的配置文件按模板补齐 |
| `tbqrfly refresh --dry-run` | 预览待新增或补齐的文件，不写入、不创建备份 |

只有 Zealot 配置的项目，默认不会新增 Beta 发布任务配置；只有 Beta 配置的项目，默认不会创建 Zealot、LLM 配置。
新增 Beta 功能可以显式运行 `tbqrfly refresh --target beta`，会保留已有飞书凭据。
新建 Beta 文件采用完整模板，包括尚未实现发布器的 Android 示例；当前只运行 iOS 时仍需按 Beta 教程移除该示例，或发布时指定 `--platform ios`。
没有 `.tbqrfly/` 的项目仍应先运行 `init`。

## 备份与恢复

实际修改前，原文件会保存到 `.tbqrfly/backups/refresh-<UTC时间>-<随机后缀>/`，终端打印具体位置。
备份目录仅当前用户可访问，备份文件权限为 `600`；备份可能包含凭据，随 `.tbqrfly/` 一起忽略，不要提交。

备份中保留被修改文件的原始内容；`manifest.json` 记录本次新增的文件。
需要撤销时，将备份中的配置文件复制回 `.tbqrfly/`，并按清单删除本次新增且不再需要的文件。
写入失败时，工具会尝试恢复本次已写入的文件；即使恢复失败，备份仍保留并给出位置。
没有变化时不重写配置，也不生成多余备份。

`refresh` 只负责补齐默认项，不验证账号权限、签名或配置是否可用于实际发布。刷新后可继续执行对应 `doctor`。

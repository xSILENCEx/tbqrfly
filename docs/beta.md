# 平台内测通道发布

`tbqrfly beta` 使用独立的 `.tbqrfly/beta.json`，固定发布 prod release，不读取
`builds.json`、`zealot.json` 或其中的前后置命令。目前实现 iOS → TestFlight；
Android → Google Play 等渠道尚未实现，选中未支持的平台或 provider 会明确报错。
平台列表由 `builds` 动态读取，未配置的平台不会参与任务。

## 初始化与运行

```bash
tbqrfly init --target beta
# 当前仅使用 iOS 时，先从 beta.json 移除尚未支持的 builds.android 示例
tbqrfly doctor --target beta
tbqrfly beta
tbqrfly beta --platform ios
tbqrfly beta --platform all
```

支持 `--project /path/to/flutter/project`。Beta 没有 `--env` 参数，不询问环境。
`init --target beta` 可用于已经配置 Zealot 的项目，仅新增 Beta 配置和 VS Code
任务 `tbqrfly: beta`，已有飞书配置不会覆盖。已有 `beta.json` 时需显式使用
`--force` 才会重写。不要为增加 Beta 支持重新运行普通的 `init --force`。

## 配置

```json
{
  "app_name": "Insvibe",
  "wait_timeout": 10,
  "terminal": "auto",
  "pre_commands": [
    "dart run tool/configure_native_sdks.dart"
  ],
  "next_commands": [],
  "builds": {
    "ios": {
      "pre_commands": [],
      "build_command": "flutter build ipa --release --flavor prod --dart-define=FLAVOR=prod",
      "export_options": "ios/ExportOptions-AppStore.plist",
      "artifacts": [
        "build/ios/ipa/*.ipa"
      ],
      "publish": {
        "provider": "testflight",
        "bundle_id": "ai.song.generator.music.maker.insvibe",
        "key_id": "${ASC_KEY_ID}",
        "issuer_id": "${ASC_ISSUER_ID}",
        "key_file": "${ASC_KEY_FILE}"
      },
      "next_commands": []
    },
    "android": {
      "pre_commands": [],
      "build_command": "flutter build appbundle --release --flavor prod --dart-define=FLAVOR=prod",
      "artifacts": [
        "build/app/outputs/bundle/prodRelease/*.aab"
      ],
      "publish": {
        "provider": "google_play",
        "package_name": "ai.song.generator.music.maker.insvibe",
        "service_account_file": "${GOOGLE_PLAY_SERVICE_ACCOUNT_FILE}",
        "track": "internal",
        "release_status": "completed"
      },
      "next_commands": []
    }
  }
}
```

前置命令示例是项目自有脚本，按实际项目调整。路径相对 Flutter 项目根目录，
文件路径支持 `~`；`${NAME}` 引用当前进程的环境变量。未设置的变量展开为空，
必需凭据缺失会在构建前报错。工具不读取项目的 `.env` 或 `.release/*.env`。
可以在本机 `beta.json` 填写凭据字段，或在启动终端预先导出上述环境变量；
`.tbqrfly/` 已加入 Git 忽略，`.p8` 私钥应放仓库外。

| 字段 | 行为 |
| --- | --- |
| `app_name` | 通知中的应用名称 |
| `wait_timeout` | 平台选择超时秒数，默认 10；超时默认 all |
| `terminal` | 与 Zealot 一致：auto / inline / system；保留多平台并行 |
| 顶层 `pre_commands` / `next_commands` | 全局前置与成功后的全局后置，各执行一次；默认空数组 |
| `builds.<platform>.pre_commands` / `next_commands` | 对应平台前置与成功后的后置；默认空数组 |
| `build_command` | 完整构建命令；默认模板使用 prod，显式 dev/debug/profile 参数会被拒绝 |
| `export_options` | iOS App Store 导出配置 plist |
| `artifacts` | 当前平台的产物路径数组，可使用项目相对路径通配符 |
| `publish` | 平台发布器配置；当前只支持 ios/testflight |
| `notify` | 可选布尔值，默认 true；凭据复用 `feishu.json`，发送文本通知 |

全局配置中不使用 `default_env`；`builds` 下直接放平台，不嵌套 dev/prod。
不使用 flavor 的项目可去掉构建命令中的 `--flavor prod`；工具无法推断自定义脚本
或项目内部的环境实现，请确保自定义构建入口实际生成生产环境包。

## iOS 准备

需要 macOS、Flutter、Git、完整 Xcode、Ruby >= 3.2、Bundler >= 2.4。
`doctor --target beta` 检查本机命令、Ruby/Bundler 版本和配置文件；不访问 Apple，
不验证证书可用性或 Apple 凭据权限。

工具自带 Fastlane 2.241.2 的 Gemfile、Gemfile.lock 与专用 Fastfile，运行时放在
`.tbqrfly/.runtime/fastlane-2.241.2/`，不修改项目的 Gemfile、Fastfile。
首次发布或依赖缺失时执行 `bundle install`，需可访问 RubyGems，且 Ruby gem
目录可写。之后使用 `bundle exec fastlane ios tbqrfly_upload` 上传。
锁文件记录 Bundler 4.0.20；Bundler 可能需要安装匹配版本，Ruby 管理器和 gem
目录权限应在打包机上预先准备好。首次安装失败会停止发布，不回退到未锁定的 Fastlane。

Apple API Key 使用团队密钥对应的 Key ID、Issuer ID 与 `.p8` 文件路径，私钥直接
由 Fastlane 读取，不写入 JSON、命令参数或工具生成的脚本。Apple API Key 用于上传，
不能替代 Xcode 签名用的 Apple Distribution 证书、私钥和 provisioning profiles。
App Store Connect 中需要预先创建相同 Bundle ID 的 App，并完成必要的账号配置。

导出配置的 `method` 必须为 `app-store-connect`（兼容 `app-store`），
`destination` 使用 `export`，`manageAppVersionAndBuildNumber` 不得开启。
不要沿用 Zealot 的 Ad Hoc 导出配置。
初始化优先使用项目已有的 `ios/ExportOptions-AppStore.plist`；若没有且可读取
Xcode Team，则生成 `.tbqrfly/ExportOptions-AppStore.plist`。没有 Team 时需自行
准备导出配置。Bundle ID 始终由用户在 `publish.bundle_id` 中填写。

## 执行与失败行为

1. 选择平台并验证选中平台的配置和工具，准备 Fastlane 依赖。
2. 执行全局前置命令，读取 `pubspec.yaml` 的版本及 Git 提交信息。
3. 各平台并行执行：平台前置 → 重新构建 → 检查本次产物 → 上传 → 平台后置。
4. 等待所有选中平台完成，包括独立系统终端；全部成功后执行一次全局后置命令。

版本与构建号取自 `pubspec.yaml`，工具不自动递增、不修改版本、不自动拉取代码。
Beta 不复用已有 IPA：从匹配产物中选择本次新增或更新的唯一文件，检查主 App 的
Bundle ID、版本号和构建号；没有本次产物或产物不唯一时停止上传。
不做旧工具的二进制 Git 提交号匹配检查。构建仍注入 `GIT_COMMIT`，不追加其他环境。

同一项目的 Beta iOS 和 Zealot iOS 发布使用共享的系统文件锁，重复启动会明确失败。
锁在进程结束时由系统释放；不同平台仍可并行。工具外直接运行的 Flutter 命令不受该锁保护。

命令列表按顺序执行，工作目录为项目根目录；不经过 shell。需要管道、重定向或
`&&` 时显式使用 `bash -c` 或脚本。前置失败停止对应任务；构建或上传失败时不执行
平台后置；任一平台失败时不执行全局后置。已经并行运行的平台不会被取消。
后置命令失败返回非零退出码，但不会撤销已完成的上传，通知会明确区分这一情况。

通知复用 `feishu.json` 的凭据，不依赖二维码、`message.md` 或 LLM。
通知缺失或失败仅警告/跳过，不改变发布退出码。Beta 的提交记录独立保存为
`.tbqrfly/beta_commit_history.json`，不会改变 Zealot 的摘要记录。

TestFlight 上传完成只表示 Apple 已接收上传，仍需等待处理并在 App Store Connect
配置测试分发。工具不自动提交外部 Beta 审核、App Store 正式审核或分配测试组，
也不自动填写出口合规信息。通知为文本，不生成即时安装二维码。

## 项目符号与扩展步骤

Dart 符号、Crashlytics、原生 SDK 配置由项目脚本负责。已有
`tool/flutter_release.py` 的项目可以将 `build_command` 的 `flutter` 替换为
`./tool/flutter_release.py` 来复用符号归档逻辑。
如果必须在构建完成、上传之前执行符号处理，可用项目构建包装脚本作为
`build_command`，将工具追加的参数转交给 Flutter，并在符号处理失败时非零退出；
`next_commands` 位于上传之后，不适合承担上传前必须完成的检查。

## 后续 Android 接入

模板已包含 `builds.android`：prod AAB 构建、产物路径、前后置命令，以及
`publish.provider: google_play`、包名、服务账号文件、`internal` track 和发布状态。
`service_account_file` 通过 `${GOOGLE_PLAY_SERVICE_ACCOUNT_FILE}` 引用本机服务账号 JSON 文件。
当前要仅使用 iOS，请使用 `--platform ios`；`doctor` 会检查所有已配置平台，
默认选择及 `--platform all` 也会包含 Android，因此运行这些命令前需移除 Android 示例。
后续实现 Android 发布器时，
发布器可参考 ai-music 的 `upload_aab`：使用服务账号上传 AAB 到 `internal` track。
当前版本尚未实现该 provider，不会调用项目 Fastlane 或静默跳过。

官方参考：[Fastlane TestFlight 上传](https://docs.fastlane.tools/actions/upload_to_testflight/)、
[Apple API Key 配置](https://docs.fastlane.tools/actions/app_store_connect_api_key/)。

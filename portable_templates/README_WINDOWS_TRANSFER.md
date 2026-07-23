# Ryzen 7 8845H 便携模型运行包

这个操作层只负责搬运、环境安装、启动/暂停和结果回传，不改变模型、分组切分、2025 锁或任何科学输入。

## 先在原电脑生成一个文件夹

1. 在模型页面点击“安全暂停”，等 `active=0`、`counts.running=0`。
2. 关闭模型 dashboard 和 watchdog。生成脚本会检查仍在运行的 Python 进程并拒绝带运行中任务打包。
3. 在项目根目录打开 Windows PowerShell 5.1，执行：

   ```powershell
   powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File ".\scripts\transfer_prepare_bundle.ps1"
   ```

默认输出是：

```text
D:\HuaweiMoveData\Users\haora\Documents\ISEF\exports\ISEF_MODEL_RUNNER_8845H
```

脚本会先运行完整 pytest 与 Ruff，然后使用显式白名单复制代码、测试、冻结模型输入和 manifests。它不会复制 `.venv`、`.git`、缓存、原始下载或凭证。它会：

- 打包经过版本校验、可随文件夹移动的 Python 3.14.4 64 位最小运行时，并保留官方安装器作为后备；
- 下载当前精确依赖版本的离线 wheelhouse；任一精确 wheel 缺失就失败；
- 生成文件级 SHA-256 bundle manifest；
- 生成并提交 `portable_relocation.json`；
- 把原电脑标记为 `transferred_out`，并创建 `RUN_DISABLED_TRANSFERRED_OUT.txt`。

当前原电脑已完成的 42 个试运行任务不会搬到游戏本。因为 relocation 进入运行指纹，游戏本会建立一个新的、可审计的 57,800-task 队列，不能把两个 run 混在一起。

复制前可以在原电脑对成品做不夺取目标所有权的验收（不会创建 queue、写 control 或启动 dashboard）：

```powershell
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File ".\exports\ISEF_MODEL_RUNNER_8845H\portable_templates\setup_and_launch.ps1" -VerifyOnly -NoPrompt -NoBrowser
```

验收会在系统临时目录创建并删除测试 venv，离线安装全部 wheel，然后验证 relocation、真实 model context 和精确的 55,645 inner + 2,155 outer task plan。

## 在游戏本上一键启动

把整个 `ISEF_MODEL_RUNNER_8845H` 文件夹复制到游戏本的任意本地 NTFS 路径；路径可以有空格。不要只复制里面的部分文件。

双击 bundle 根目录的：

```text
START_HERE.cmd
```

启动器兼容 Windows PowerShell 5.1，并按顺序：

1. 校验所有不可变文件、Python 安装器、wheel 和 relocation manifest；
2. 确认这不是原电脑，也没有被第二台目标机器激活；
3. 直接使用 bundle 内的精确 Python 3.14.4 运行时，不依赖游戏本已安装的 Python；
4. 在 bundle 内新建 `.venv-portable`，仅从离线 wheelhouse 安装精确依赖；
5. 运行 relocation、dashboard、SQLite queue 的定向测试和真实 context authentication smoke test；
6. 新建 fresh queue，并保持 `paused`；
7. 在 `http://127.0.0.1:8766/` 启动 dashboard。

启动时可选 6 或 8 workers，默认 6；选择会写入 `portable_runtime.json` 和 `dashboard_control.json`。页面打开后仍保持暂停，确认 workers 后再点击“开始/继续”。

## 暂停、关机与恢复

- 只能从 dashboard 点击“安全暂停”。
- 等 `active=0` 后再关机或移动文件夹。
- 不要在任务运行时强制结束 Python、复制 SQLite 或同步整个目录。
- 重启后再次双击 `START_HERE.cmd`；已保存的 6/8 选择、任务状态和结果会恢复。
- dashboard watchdog 会重启意外退出的页面服务；协调器只在 persisted desired state 为 `running` 时恢复。

## 完成后打包带回

当页面显示 `complete` 时，双击：

```text
PACKAGE_RESULTS.cmd
```

默认会在 bundle 的上一级目录生成 `ISEF_model_results_时间戳.zip`。ZIP 包括：

- 一致性 SQLite backup、run manifests、outer fragments 和 status；
- `data/processed/model_evaluation/` 最终 OOF 与审计指标；
- bundle manifest、relocation manifest、精确依赖/Python 版本和文件 SHA-256；
- result manifest，其中锁定 bundle SHA、relocation commit、run ID 和全部返回文件哈希。

只有诊断或中途换机时才使用：

```powershell
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File ".\portable_templates\package_results.ps1" -AllowPausedCheckpoint
```

它同样要求 `active=0`。不要在结果 ZIP 返回并验证前删除原电脑的 `RUN_DISABLED_TRANSFERRED_OUT.txt` 或恢复原电脑运行。

## 两机互斥边界

portable launcher 用 source/target MachineGuid 的 SHA-256 和 `transfer_id` 锁定唯一目标机器；复制已激活的 bundle 到第二台机器会拒绝启动。原电脑的源队列保持暂停，并有 `transferred_out` marker。

正常的 grouped coordinator 与 dashboard Start API 都会检查 `RUN_DISABLED_TRANSFERRED_OUT.txt` 并拒绝源机启动；这不是单纯警告。手工删除 marker 或修改执行守卫属于显式破坏审计所有权，不应执行。

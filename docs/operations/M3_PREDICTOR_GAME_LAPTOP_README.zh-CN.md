# M3 source predictor 游戏本迁移包（历史说明）

> 此流程已经完成，仅为操作溯源而保留。它不是当前实验入口，也不适用于普通 Git clone。

1. 把整个 `M3_PREDICTOR_GAME_LAPTOP` 文件夹复制到游戏本本地 SSD。
2. 确保办公本不再运行本项目；两台机器绝不能同时启动同一队列。
3. 游戏本接通电源，关闭自动睡眠，保证至少 16 GB 内存和 15 GB 可用空间。
4. 运行迁移包内的 `RUN_M3_PREDICTOR_GAME_LAPTOP.cmd`。

启动器会先认证授权与持久队列，然后使用单个 acquisition、4 个 Sentinel
波段下载线程和 1 个计算线程，从原 SQLite 断点继续。联网阶段完成并认证后，
会自动进入离线组装。

迁移时的快照为 `75 complete / 10 pending / 0 running / 0 lease`。Houston 与
Chicago 已完成的 acquisition cache 会直接复用，不重新下载。

如果窗口报错，不要删除 `data`、SQLite、cache 或 manifest；应保留整个迁移包
并检查错误。该迁移任务现已完成，公共仓库不包含其运行时和数据包。

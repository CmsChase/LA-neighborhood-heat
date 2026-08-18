M3 source predictor 游戏本迁移包

1. 把整个 M3_PREDICTOR_GAME_LAPTOP 文件夹复制到游戏本本地 SSD。
2. 确保办公本不再运行本项目；两台机器绝不能同时启动同一队列。
3. 游戏本接通电源，关闭自动睡眠，保证至少 16 GB 内存和 15 GB 可用空间。
4. 双击 RUN_M3_PREDICTOR_GAME_LAPTOP.cmd。

启动器会先认证授权与持久队列，然后用：
- 单个 acquisition；
- 4 个 Sentinel 波段下载线程；
- 1 个计算/本地原生线程；
从原 SQLite 断点继续。联网阶段完成并认证后，会自动进入离线组装。

当前迁移快照：75 complete / 10 pending / 0 running / 0 lease。
Houston 与 Chicago 已完成的 acquisition cache 会直接复用，不重新下载。

如果窗口报错：不要删除 data、SQLite、cache 或 manifest；保留整个文件夹并反馈错误。

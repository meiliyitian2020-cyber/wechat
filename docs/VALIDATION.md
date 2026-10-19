# 验证记录

2026-10-19。本次执行环境：Linux x86_64，无微信客户端、账号数据、Windows 或 Wine。
用户确认有 Windows 测试机，但本环境没有该机器的远程执行能力。

## 已执行

`PYTHONPATH=/workspace/wechat/.deps:/workspace/wechat python -m pytest -q /workspace/wechat/tests`

结果：11 passed，0 failed，0 skipped。

覆盖：
- 用户图片裁切为 128×128 PNG；持久保存 BLOB 与 SHA256；源文件删除、Store 重建后副本仍存在。
- 不覆盖首次备份信息；相同好友 ID 的不同账号隔离；删除配置不影响其他账号。
- 无成功应用记录时不会误报失效；字节一致不被声称为客户端有效；读取异常不泄露错误中的敏感值。
- 写入和恢复明确拒绝。
- 人工 SQLite 联系人数据查询；只读连接拒绝 DELETE；源数据库字节不变；排除聊天数据库。
- 无显示器 Qt 界面初始化、已保存配置恢复和昵称/备注搜索。

## 未执行／未通过验收

- Windows 11 25H2 识别、版本资源读取与实际数据目录发现。
- 微信 4.1.13.65 密钥获取、真实联系人与头像读取。
- 真实客户端头像/昵称修改及其他设备影响验证。
- 备份、加密写回、回滚、原头像恢复、失效弹窗与一键重应用（写后端尚未实现）。
- Windows PyInstaller 构建、单文件 EXE 双击及退出进程检查。
- GitHub Actions 工作流运行。

这些均不是已通过项目；目前只读界面不是最终产品。

## 第二轮验证结果（取代上述 11 项计数）

安装固定版本依赖后，在独立 venv 执行：

```
/workspace/.venvs/wechat/bin/python -m pytest -q
```

结果：**26 passed，0 failed，0 skipped**。
新增 9 项备份测试和 6 项真实 SQLCipher 引擎人工数据库测试。
备份测试涵盖 DB/WAL/SHM、哈希、源文件变化、进程重启、符号链接、越界路径和清理。
SQLCipher 实验涵盖加密暂存写入/解密读回、事务异常回滚、WAL 拒绝、逐页损坏、错误密钥和缺失好友。
使用 SQLCipher 4.12.0 community，未使用任何真实微信数据。

Windows 构建不安装此 Linux 研究依赖，因此该组实验在 Windows 测试中会显示为 skipped，
不能将 Windows 其余测试通过解释为 SQLCipher Windows 写回能力已验证。
此前列出的 Windows 和真实客户端未验证事项仍然全部有效。

## Windows 用户反馈与跨平台依赖更新

用户反馈：本地可以识别微信环境、读取联系人、当前昵称与头像。
这是用户执行后的反馈，不是本会话直接操作 Windows 所得的结果。
该反馈未包含 OS build、确切客户端版本、截图或日志，因此不据此扩大为特定版本的完整适配结论。
真实修改、客户端显示、远端设备影响和 EXE 验收仍未通过。

已通过 PyPI 核对官方 coleifer/sqlcipher3 0.6.4 提供 Windows CPython 3.12 x64 wheel，
并下载核对其中的原生 .pyd 和驱动许可证。这只证明构建依赖可取得，不证明 Windows 加载成功。
统一使用 sqlcipher3==0.6.4；在本环境实际运行 26 passed，0 failed，0 skipped。
之前关于 Windows 不安装 SQLCipher、对应测试会跳过的说明不再适用于最新配置：
最新 requirements 包含引擎，加密测试不再使用 importorskip，缺少引擎会让测试失败。

## Windows 用户运行日志（提交 76d5e13）

用户提供的日志：Windows / Python 3.12.10，26 项收集，25 passed、1 failed。
6 项 SQLCipher 暂存实验全部通过，证明该机器可以加载引擎并完成人工加密库读写、回滚与校验。
失败发生在 test_reject_symlink 的测试准备阶段：Windows 禁止普通用户创建符号链接，WinError 1314。
并非备份模块接受了危险路径，也不是加密写入测试失败。

修复：仅在 Windows 创建真实符号链接遇到 WinError 1314 时将对应集成测试记为 skipped；
其他错误仍然失败。另增一项无需创建权限的链接检测分支测试，检查拒绝操作、不创建备份及源文件不变。
修复后 Linux：27 passed。受限 Windows 预期为 26 passed、1 skipped，尚待该版本日志确认。
不要求提升权限或打开开发者模式。真实微信数据库修改和客户端显示效果仍未验证。

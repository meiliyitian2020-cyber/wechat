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

## 实验性离线写回实现

新增 Windows 独占文件句柄、持久加密备份/操作日志、暂存 SQLCipher 事务修改、逐页 HMAC、
实际写回后重新读出验证、失败精确回滚、未知中断状态阻止后续写入。
GUI 应用/重新应用/清除头像缓存/故障恢复已接入该后端，不再调用原先占位 Writer。
联系人读取时只在内存保留头像库密钥；进程退出后不落盘，重新打开必须登录并读取账号。
当前头像检测使用自行稳定复制的 main+WAL 并通过 SQLCipher 读取，不使用上游可能过时的解密缓存。
昵称纯本地同步语义未证明，禁用；未修改 contact.db。

当前 Linux 结果：39 passed、1 skipped。跳过项为 Windows CreateFile 独占句柄集成测试。
新增测试实际修改人工加密文件，验证目标记录和其他好友、持久备份、提交前/后故障回滚、
WAL 拒绝、运行状态拒绝、故障恢复/外来修改拒绝、WAL 观察、失效判定、GUI 真实后端调用与取消操作。
这不等于原版微信显示、收发消息、其他设备影响或 Windows 独占访问测试已经通过。
新版 Windows 自动构建将执行该句柄测试，构建结果另行确认。

### Windows CI 已完成第一轮原生构建

GitHub Actions run 39211328364，提交 10dcd3e，状态 SUCCESS，耗时 1m39s。
该流程执行了 Windows 人工数据测试（含 WindowsFile 独占句柄）和 PyInstaller 单文件构建，
并上传 EXE artifact。这证明首次 Windows 原生构建通过，不等于真实微信客户端显示已通过。
后续提交新增 EXE 内置 --self-test：构建后实际启动 EXE，对人工加密库执行独占写入、读回和头像记录清除。
该新增自检需以对应新构建结果为准，不能从第一次构建成功推断。

### 最终本轮 Windows 测试包结果

提交 bf07176，GitHub Actions run 39211673240：SUCCESS，1m25s。
流程依次通过 Windows 人工数据测试、PyInstaller 单文件打包、启动实际 EXE --self-test 并检查 ok=true，
然后才上传 artifact 12168305639（页面显示约 59 MB）。
该自检在 Windows 临时目录创建人工加密库，实际使用 Windows 独占句柄完成写入、备份核验、
观察读回和头像记录清除；不访问用户微信数据。不能替代真实微信的三处头像显示和收发消息验收。
下载： https://github.com/meiliyitian2020-cyber/wechat/actions/runs/39211673240/artifacts/12168305639
GitHub artifact 下载需要登录，解压后运行 dist/WeChatLocalProfileEditor.exe。

## 4.1.15.65 候选检查版本

Linux：52 passed，1 skipped（Windows 独占句柄测试）。
新增版本策略测试、读取后升级拒绝、新版原库写入拒绝、实际加密副本探测、非目标行与隐私验证、
未知触发器拒绝和 GUI 副本检查/写入阻断测试。
EXE 内置自检新增副本兼容性检查，Windows 构建将实际运行该路径；结果以新 Actions 为准。
4.1.15.65 的真实联系人读取、副本探测、原版客户端显示均尚未取得新实测结果，原库写入默认禁用。

### 一次性实验写入流程

新版默认写入禁用，新增本机副本检查证明及用户明确选择后的单次实验入口。
测试覆盖无证明拒绝、不同图片/超时/源文件变化拒绝、证明单次使用、删除操作独立证明、
同版本中断恢复、虚拟头像表拒绝、GUI 明确选择试验按钮后调用实际后端。
当前 Linux：62 passed，1 skipped（Windows 独占句柄）。
EXE 自检扩展为：模拟新版上下文，完成副本检查，再使用一次性证明写入人工库并测试对应恢复。
不访问任何真实微信数据，4.1.15.65 实际读取和客户端显示仍需用户运行本包验证。

打包后的自检另加入 Qt 主界面创建、显示、事件循环处理和退出，使用人工配置且禁止发现真实账号。
Windows CI 对 EXE 自检设 90 秒超时，避免 Qt 启动错误导致无限等待。

### 4.1.15.65 实验测试包构建确认

代码提交 391fa03，GitHub Actions run 39215740904：SUCCESS，耗时 1m49s。
Windows 自动化测试、PyInstaller、实际 EXE 的副本检查/一次性证明写入/恢复自检、
以及打包 Qt 界面启动和关闭检查均通过。全部使用人工数据，没有真实微信账号。
artifact 12169612413，约 59 MB：
https://github.com/meiliyitian2020-cyber/wechat/actions/runs/39215740904/artifacts/12169612413
此结果不是 4.1.15.65 原版客户端渲染通过的证据。该机器上的真实读取、实际数据副本兼容性及
会话列表/聊天窗口/好友资料页显示仍需用户运行上述 EXE 确认，界面始终区分数据库验证与显示验证。

## 4.1.15.56 版本识别修正

用户最新截图显示客户端为 4.1.15.56；此前测试包候选列表仅含 4.1.15.65，错误提示停在版本识别阶段。
补入 4.1.15.56 的读取候选入口，保留强制副本检查、单次凭据、显式确认和备份/回滚，未直接加入 WRITE_VERSIONS。
两种 4.1.15 版本均运行单次写入凭据测试；EXE 自检使用 4.1.15.56 人工上下文。
版本错误现在直接显示检测到的版本和具体策略原因，不再统一提示账号/进程权限问题。
Linux：73 passed、1 skipped（Windows 独占句柄）。4.1.15.56 真实数据读取和客户端头像显示仍待用户验证。

4.1.15.56 修正版提交 050c492：Windows Actions run 39217721788 SUCCESS（1m36s）。
Windows 自动化测试、EXE 的加密事务/新版单次凭据流程与打包界面启动退出自检通过。
artifact 12170555922，约 59 MB：
https://github.com/meiliyitian2020-cyber/wechat/actions/runs/39217721788/artifacts/12170555922
真实 4.1.15.56 客户端读取及显示仍需用户实测。

## WAL/SHM 修正版

Linux：92 passed，2 skipped（Windows 单文件及三文件独占句柄集成）。
新增真实 SQLCipher WAL 场景：最新目标/其他好友数据仅在 WAL 中、空 WAL、仅 SHM、
逐文件写入中途异常回滚、整组备份、日志变化导致凭据失效、已知混合文件中断恢复、
WAL 文件头/滚动校验和/加密 HMAC/截断损坏拒绝、旧 epoch 尾部和未提交事务处理。
副本探测前后主库/WAL/SHM 三者字节完全不变，成功写回保留其他好友在 WAL 中的最新提交。
EXE 自检也创建离线残留 WAL/SHM，实际使用 Windows 三文件独占句柄检查、写回、备份校验，再执行新版试验和界面自检。
新增相同配置重复保存保留上次应用历史，避免重试副本检查丢失失效检测依据；探测失败也保留自定义头像配置。
Windows 本轮结果需以新构建为准，真实微信显示仍未验证。

WAL 修正版提交 c57dc96：Windows Actions run 39220564386 SUCCESS（1m44s）。
Windows 测试包含主库/WAL/SHM 三文件独占处理；打包 EXE 实际完成残留日志副本合并、
整组三文件备份、写回、单次检查凭据及恢复自检，并通过 Qt 界面启动退出检查。
artifact 12171616801，约 59 MB：
https://github.com/meiliyitian2020-cyber/wechat/actions/runs/39220564386/artifacts/12171616801
这些结果使用人工数据库，真实微信客户端显示效果仍需实测。

## 4.1.15.56 真实客户端反馈：显示替换失败
用户报告试验写入提示完成，但重新登录微信后仍显示原头像；登录转圈较久。
因此当前路线未通过真实客户端头像替换验收。转圈不能证明校验或重新下载机制。
新增“登录后头像诊断（只读）”：使用稳定主库/WAL副本，区分自定义字节仍存在、
原始字节恢复、其他字节、记录缺失、无法读取。报告不包含身份、图片、路径、密钥。
该检查只能定位数据库层是否变化，不能确定缓存读取优先级或同步原因，不是替换修复。

诊断版 a966db9：Linux 97 passed、2 skipped；Windows Actions run 39223175102 SUCCESS，
1m34s，包含测试、单文件打包和人工数据库 EXE 自检。artifact 12172074318。
下载：https://github.com/meiliyitian2020-cyber/wechat/actions/runs/39223175102/artifacts/12172074318
尚未收到诊断版在真实账号上的登录后报告；头像显示替换仍未修复。

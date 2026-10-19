# 第三方代码与许可证

实际复用：

- https://github.com/fanyuantaier/wechatauto-replica
- 固定提交：d322c4a9b1124228820bab363e5676016f188fc6
- 来源：wechatauto/db.py → editor/vendor/reader.py
- Apache License 2.0，完整许可证保存在 wechatauto-replica-LICENSE。
- 本地修改：替换 logger import，禁用其全局日志副作用；kernel32 初始化仅限 Windows，允许非 Windows 人工 schema 测试。
- 适配器另行限制为 contact.db/head_image.db，关闭密钥落盘、其他账号自愈和自动诊断；读取结果只作有限范围证据。
- 上游其他 UI、媒体、消息自动化依赖未引入，无网络上传接口。

仅调查、未复制代码：

- https://github.com/xxxxshuai/WeChatMsg （MIT，14b466dcc4c60b43d9d79301e489c8d6029daed4）。
- https://github.com/ops120/wechat-local-viewer （MIT，de6a3f09fab43a02e786826b3085545dec9615f0）。

直接运行依赖：PySide6（Qt/PySide 的 LGPLv3/GPLv3/商业许可选项）、Pillow（HPND 等组件许可）、
cryptography（Apache-2.0 或 BSD-3-Clause，底层库另有许可）、psutil（BSD-3-Clause）。
打包依赖：PyInstaller（GPL-2.0-or-later 带 bootloader 例外）。测试依赖：pytest（MIT）。
发布前仍须核查所选二进制 wheel 内 LICENSE/NOTICE、Qt 插件、动态库及 LGPL 义务，随 EXE 提供对应通知与适用源码/重新链接说明。
本次未完成 EXE 分发合规审查，不能把仅附本 NOTICE 当作已满足全部二进制分发义务。

可选 Linux 研究依赖：sqlcipher3-binary 0.6.0（Python 驱动为 zlib/libpng 类宽松许可，
所捆绑 SQLCipher community 为 BSD 风格许可，OpenSSL 等另有许可）。
当前 Windows 构建不安装、不分发该依赖；引入 Windows SQLCipher 二进制前需核查具体产物及许可。

最新依赖变更：已替换 sqlcipher3-binary 为官方 coleifer/sqlcipher3 0.6.4，
来源 https://github.com/coleifer/sqlcipher3 ，驱动许可证为 zlib/libpng，原文见 sqlcipher3-LICENSE。
Windows wheel 已取得并检查，尚未执行；最新 Windows requirements 包含此依赖。
驱动许可证不涵盖全部静态链接组件，发布 EXE 前仍需检查 SQLCipher/OpenSSL 的通知义务。

实验版 EXE 的构建附带已安装运行依赖中的 LICENSE/NOTICE/COPYING 文件（tools/collect_licenses.py）。
SQLCipher 许可文本摘自 sqlcipher3 0.6.4 源码发行包中的 vendor/sqlite3.c，见 SQLCipher-LICENSE。
Windows sqlcipher3 wheel 内的版本字符串为 OpenSSL 3.6.0；附其官方 tag 的 LICENSE.txt。
Qt/PySide 源码： https://code.qt.io/cgit/pyside/pyside-setup.git/tree/?h=v6.8.3 和
https://download.qt.io/archive/qt/6.8/6.8.3/ 。提供完整应用源码与构建命令，允许修改和重新打包所用动态库；
不得将应用分发条款理解为限制适用 LGPL 权利。Qt DLL 在 onefile 启动时解压并动态加载。

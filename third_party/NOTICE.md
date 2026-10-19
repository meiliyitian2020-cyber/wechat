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

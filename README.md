# 微信本地好友身份修改器（开发中）

**当前交付未达到修改器验收标准。没有可用的已验证 EXE。**
当前源码提供中文只读验证界面、实验性 Windows 联系人/头像读取适配器和配置持久保存。
头像替换、昵称替换、恢复与重新应用均尚未实现；点击相关按钮会明确拒绝写入。
不能将本界面保存配置、显示头像或数据库字段一致理解为原版微信显示已修改。

## 当前使用方式（开发者）

Windows x64 Python 3.12：

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt pytest==8.4.2
.\.venv\Scripts\python main.py
```

先登录微信 4.1.13.65。工具仅允许该版本的实验性读取，其他版本或未知版本拒绝读取。
可选择自定义数据根目录和账号；多账号不会静默选择其他账号。
选择好友及 JPG/PNG/WEBP 图片后，“保存配置”只把副本保存在工具自己的配置库。
“清除选中配置”只删除工具记录，不修改微信，不代表恢复头像。
配置存放于 `%LOCALAPPDATA%\WeChatLocalProfileEditor\profiles.sqlite3`，其中包含昵称与头像，应按个人数据保护。
不读取聊天消息，不联网传输个人数据，不安装服务、任务、驱动或启动项。

只读解密可能需要与微信一致的权限。不要为绕过错误关闭安全软件。
解密快照暂存在独立临时目录，正常读取完成后清理；异常断电或强制终止可能留下快照，需要本机手动清理。
密钥只用于内存验证，不调用上游明文密钥保存逻辑。Python 无法保证内存密钥完全擦除。

## Windows 单文件构建

已提供 `.github/workflows/windows-build.yml` 手动构建工作流；尚未在 Windows 执行。
将源码推送至你的 GitHub 仓库后，在 Actions 手动运行 `Windows read-only validation build`，下载 artifact。
也可在 Windows 开发环境安装 `pyinstaller==6.16.0` 后执行：

```powershell
python -m pytest -q
python -m PyInstaller --noconfirm --clean --onefile --windowed --name WeChatLocalProfileEditor --add-data "third_party;third_party" main.py
```

输出预期为 `dist\WeChatLocalProfileEditor.exe`。这是**只读验证版**，不是已完成修改器。
构建命令和 Actions 尚未执行，不能据此声称 EXE 已生成或 Windows 双击测试通过。
Linux PyInstaller 不能直接产出 Windows EXE，本次没有将 Linux 二进制伪装为 EXE。

## 验证

```sh
python -m pytest -q
```

Linux 无显示器测试需设置 `QT_QPA_PLATFORM=offscreen`。
测试只使用人工构造数据；详见 [验证记录](docs/VALIDATION.md)。
技术障碍见 [可行性调查](docs/FEASIBILITY.md)，代码复用见 [第三方说明](third_party/NOTICE.md)。

## 加密格式研究（不启用真实写入）

Linux 开发环境可安装 `requirements-research.txt` 后运行测试。
当前共 26 项通过，其中 6 项使用真实 SQLCipher 引擎处理人工加密数据库。
已验证暂存副本的事务更新、全页 HMAC 与上游解密读回；不涉及原版微信显示结果。
备份模块已实现稳定离线副本和校验，但真实写入仍需独占锁和客户端适配。

### 最新依赖状态

现已改用官方 `sqlcipher3==0.6.4`，其提供 Windows Python 3.12 x64 wheel。
安装 requirements.txt 即包含加密引擎；先前“Windows 不安装研究引擎”的说明已被此更新取代。
自动化加密测试不再允许因缺少引擎而跳过，但 Windows 运行结果尚未取得。
用户已反馈本地环境识别和联系人/昵称/头像读取成功；真实替换仍未实现。

import os
from pathlib import Path
from PySide6.QtCore import QThread, Signal, Qt
from PySide6.QtGui import QPixmap, QIcon
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
    QHBoxLayout, QLabel, QPushButton, QLineEdit, QComboBox, QListWidget,
    QListWidgetItem, QFileDialog, QMessageBox)
from .core import Store, normalize_image, Writer, UnsupportedWrite, State
from .reader import Session, auto_detect_db_dir, list_accounts
from .windows import discover, WRITE_VERSIONS, READ_VERSIONS, VersionError
from .offline import OfflineAvatar, OfflineError
from .compatibility import CompatibilityError
from .cipher_stage import CipherError
from .observe import read_current

class Loader(QThread):
    ready = Signal(object)
    failed = Signal(str)
    def __init__(self, root, account):
        super().__init__()
        self.root, self.account = root, account
    def run(self):
        session = None
        stage = '版本识别'
        try:
            executable, detected_version = discover()  # Fail closed on unsupported versions.
            stage = '所选账号密钥读取'
            session = Session(self.root, self.account)
            stage = '联系人查询'
            contacts = session.reader.contacts()
            avatars = {}
            for contact in contacts:
                try:
                    avatars[contact['username']] = session.reader.avatar(contact['username'])
                except Exception:
                    avatars[contact['username']] = None
            self.ready.emit((self.account, contacts, avatars, session.write_context(executable, detected_version)))
        except Exception as exc:
            # Only expose stage and exception class, never raw keys or paths.
            if isinstance(exc,VersionError):
                self.failed.emit(f'读取阶段：{stage}。{exc}')
            else:
                versions='、'.join(sorted(READ_VERSIONS))
                self.failed.emit(f'读取阶段：{stage}；错误类型：{type(exc).__name__}。请确认微信版本为 {versions}、已登录、账号目录正确且进程权限一致。新版先做副本检查。')
        finally:
            if session:
                session.close()

class Window(QMainWindow):
    def __init__(self, store):
        super().__init__()
        self.store, self.contacts, self.avatars, self.account = store, [], {}, None
        self.worker = None
        self.contexts = {}
        self.offline = OfflineAvatar(store.path.parent / 'backups')
        self.expired = []
        self.pending_avatar = None
        self.setWindowTitle('微信本地身份工具 · 实验性离线头像替换 · WAL修正版')
        self.resize(1020, 640)
        box = QWidget(); self.setCentralWidget(box)
        layout = QVBoxLayout(box)
        self.info = QLabel('正在识别微信环境……'); layout.addWidget(self.info)
        layout.addWidget(QLabel('4.1.13.65：实验性写回；4.1.15.56 / 4.1.15.65：先检查副本，通过后可仅本次试验写入。客户端显示待实测，昵称未适配。'))
        top = QHBoxLayout(); layout.addLayout(top)
        self.directory = QLineEdit(); self.directory.setPlaceholderText('微信数据根目录（其下包含各账号目录）')
        top.addWidget(self.directory)
        self.button(top, '选择数据目录', self.choose_dir)
        self.accounts = QComboBox(); top.addWidget(self.accounts)
        self.button(top, '读取联系人', self.load)
        body = QHBoxLayout(); layout.addLayout(body)
        left = QVBoxLayout(); body.addLayout(left, 3)
        self.search = QLineEdit(); self.search.setPlaceholderText('搜索昵称、备注、微信 ID')
        self.search.textChanged.connect(self.render); left.addWidget(self.search)
        self.items = QListWidget(); self.items.currentItemChanged.connect(self.select); left.addWidget(self.items)
        right = QVBoxLayout(); body.addLayout(right, 2)
        self.preview = QLabel('原头像'); self.preview.setMinimumSize(128,128); right.addWidget(self.preview)
        self.identity = QLabel('请选择好友'); self.identity.setWordWrap(True); right.addWidget(self.identity)
        self.desired_preview = QLabel('未选择自定义头像'); right.addWidget(self.desired_preview)
        self.name = QLineEdit(); self.name.setPlaceholderText('昵称纯本地替换尚未适配'); self.name.setEnabled(False); right.addWidget(self.name)
        self.button(right, '选择新头像', self.choose_image)
        self.button(right, '保存配置（不会修改微信）', self.save)
        self.button(right, '应用至微信', self.apply)
        self.button(right, '新版兼容性检查（不写原库）', self.probe)
        right.addStretch()
        bottom = QHBoxLayout(); layout.addLayout(bottom)
        self.button(bottom, '重新检查配置', self.check)
        self.button(bottom, '重新应用', lambda: self.reapply_expired() if self.expired else self.apply())
        self.button(bottom, '清除选中配置', self.clear)
        self.button(bottom, '恢复当前真实头像', self.restore)
        self.button(bottom, '故障恢复', self.recover)
        self.status = QLabel(); self.status.setWordWrap(True); layout.addWidget(self.status)
        # Every open checks all saved configurations, even accounts not readable now.
        self.check()
        try:
            path, ver = discover(); self.info.setText(f'微信版本：{ver} | {path}')
            root = auto_detect_db_dir()
            if root:
                self.directory.setText(root); self.scan()
                if self.accounts.count() == 1:
                    self.load()
        except Exception as exc:
            self.info.setText(str(exc))

    @staticmethod
    def button(layout, text, callback):
        b = QPushButton(text); b.clicked.connect(callback); layout.addWidget(b)

    def choose_dir(self):
        directory = QFileDialog.getExistingDirectory(self, '选择微信数据根目录')
        if directory:
            self.directory.setText(directory); self.scan()

    def scan(self):
        self.accounts.clear()
        for row in list_accounts(self.directory.text()):
            self.accounts.addItem(row['account'], row['account'])

    def load(self):
        if self.worker and self.worker.isRunning():
            return
        account = self.accounts.currentData()
        if not account:
            self.status.setText('请先选择包含有效账号的数据目录'); return
        self.contacts, self.avatars, self.account = [], {}, None
        self.render()
        self.status.setText('读取中；只访问联系人和头像库，不读取聊天消息。')
        self.worker = Loader(self.directory.text(), account)
        self.worker.ready.connect(self.loaded); self.worker.failed.connect(self.status.setText)
        self.worker.start()

    def loaded(self, result):
        self.account, self.contacts, self.avatars = result[:3]
        if len(result)>3 and result[3] is not None:
            self.contexts[self.account]=result[3]
        self.render()
        self.status.setText(f'读取 {len(self.contacts)} 个联系人；头像来自解密快照，不能证明微信显示结果。')
        self.check()

    def render(self):
        records = {(r['account'],r['friend']):r for r in self.store.rows()}
        query = self.search.text().casefold()
        self.items.clear()
        contacts = sorted(self.contacts, key=lambda c: (self.account,c['username']) not in records)
        for c in contacts:
            if query not in ' '.join(str(v or '') for v in c.values()).casefold(): continue
            r = records.get((self.account,c['username']))
            text = f"{c['remark'] or c['nick_name'] or c['username']}\n{c['username']} · " + (f"已配置 / {r['state']}" if r else '未修改')
            item = QListWidgetItem(text); item.setData(Qt.ItemDataRole.UserRole,c)
            pix = QPixmap()
            if self.avatars.get(c['username']):
                pix.loadFromData(self.avatars[c['username']]); item.setIcon(QIcon(pix))
            self.items.addItem(item)

    def selected(self):
        item = self.items.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def select(self, *_):
        self.pending_avatar = None; self.name.clear(); self.preview.setText('无可读取头像')
        self.desired_preview.setText('未选择自定义头像')
        c = self.selected()
        if not c: return
        self.identity.setText(f"昵称：{c['nick_name']}\n备注：{c['remark']}\nID：{c['username']}")
        data = self.avatars.get(c['username'])
        if data:
            pix = QPixmap(); pix.loadFromData(data); self.preview.setPixmap(pix.scaled(128,128,Qt.AspectRatioMode.KeepAspectRatio))
        for row in self.store.rows():
            if (row['account'],row['friend']) == (self.account,c['username']):
                self.name.setText(row['desired_name'] or '')
                self.pending_avatar = row['desired_avatar']; self.show_desired()
                self.status.setText(row['scope']); break

    def show_desired(self):
        if self.pending_avatar:
            pix = QPixmap(); pix.loadFromData(self.pending_avatar); self.desired_preview.setPixmap(pix)

    def choose_image(self):
        path,_ = QFileDialog.getOpenFileName(self,'选择自定义头像','','图片 (*.jpg *.jpeg *.png *.webp)')
        if path:
            try:
                self.pending_avatar = normalize_image(Path(path)); self.show_desired()
            except Exception:
                QMessageBox.warning(self,'图片无效','无法读取图片，支持 JPG、PNG、WEBP。')

    def save(self):
        c = self.selected()
        if not c: return
        try:
            self.store.save(self.account,c['username'],c['nick_name'] or '',self.avatars.get(c['username']),
                            self.name.text().strip() or None,self.pending_avatar)
            self.render(); self.status.setText('配置已持久保存；此步骤未写入微信。')
        except ValueError as exc:
            QMessageBox.warning(self,'无法保存',str(exc))

    def check(self):
        observations={}
        for account,context in self.contexts.items():
            friends=[r['friend'] for r in self.store.rows() if r['account']==account]
            if not friends: continue
            try:
                observations[account]=read_current(context,friends)
            except Exception:
                pass
        results=self.store.verify_all(lambda account,friend: observations.get(account,{}).get(friend))
        self.expired=[(a,f) for a,f,state,scope in results if state==State.INVALID]
        self.status.setText(f'检查 {len(results)} 项配置，发现 {len(self.expired)} 项本地替换已失效。数据库一致也不能证明客户端显示一致。')
        self.render()
        if self.expired:
            box=QMessageBox(self)
            box.setWindowTitle('检测到本地头像替换失效')
            box.setText('检测到以下好友的本地头像已发生变化，是否重新应用之前保存的自定义头像？\n'+
                        '\n'.join(friend for _,friend in self.expired))
            reapply=box.addButton('一键重新应用',QMessageBox.ButtonRole.AcceptRole)
            box.addButton('暂时忽略',QMessageBox.ButtonRole.RejectRole)
            details=box.addButton('查看详情',QMessageBox.ButtonRole.ActionRole)
            box.exec()
            if box.clickedButton()==reapply: self.reapply_expired()
            elif box.clickedButton()==details:
                QMessageBox.information(self,'验证范围','检测比较当前头像库（包含稳定复制的 WAL）与已保存图片。不能证明客户端缓存显示，也不代表好友主动更新头像。')

    def context(self):
        context=self.contexts.get(self.account)
        if context is None:
            raise OfflineError('请先登录微信并读取此账号联系人，以取得本次运行所需的内存密钥')
        if self.directory.text() and Path(self.directory.text()).resolve()!=context.account_root.parent.resolve():
            raise OfflineError('数据目录已变化，请重新读取联系人')
        if self.accounts.currentData()!=self.account:
            raise OfflineError('所选账号已变化，请重新读取联系人')
        return context

    def confirm_write(self,text):
        return QMessageBox.question(self,'实验性头像操作',text+
            '\n请保持本工具打开，完全退出微信（含托盘）后再继续。工具不会自动结束微信。\n会建立加密备份；客户端显示效果仍需确认，昵称不会修改。',
            QMessageBox.StandardButton.Ok|QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel)==QMessageBox.StandardButton.Ok

    def apply(self, candidate_trial=False):
        c=self.selected()
        if not c: return
        avatar=self.pending_avatar
        if not avatar:
            QMessageBox.information(self,'请选择头像','先选择一张自定义头像，或选择已有头像配置的好友。');return
        try:
            context=self.context()
            if context.detected_version not in WRITE_VERSIONS and not candidate_trial:
                raise OfflineError('此版本默认原始库写入尚未开放。请先运行新版兼容性检查，通过后选择“仅本次试验应用”。')
            if not self.confirm_write('将替换选定好友在本机头像库中的头像。'): return
            # Persist the selected image before any write so retry never needs upload.
            self.store.save(self.account,c['username'],c['nick_name'] or '',self.avatars.get(c['username']),self.name.text().strip() or None,avatar)
            backup=self.offline.apply(context,c['username'],avatar,_candidate_trial=candidate_trial)
            self.store.record_applied(self.account,c['username'],backup)
            self.render()
            self.status.setText('加密头像库已写入并校验，备份已保存。现在可启动微信检查会话列表、聊天窗口和资料页；客户端生效尚待确认。')
            QMessageBox.information(self,'数据库写入完成','头像库已写入并校验。请重新打开微信检查实际头像。尚不能将数据库成功等同于客户端显示成功。')
        except Exception as exc:
            self.operation_error(exc)

    def probe(self, _restore=False):
        import json
        import uuid
        c=self.selected()
        if not c or (not self.pending_avatar and not _restore):
            QMessageBox.information(self,'请选择测试对象','先选择一个测试好友和自定义头像，再运行副本检查。');return
        try:
            context=self.context()
            if QMessageBox.question(self,'新版兼容性检查',
                '保持工具打开，完全退出微信及托盘后再确认。\n仅在临时加密副本中测试修改，不写入原始微信数据库。',
                QMessageBox.StandardButton.Ok|QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel)!=QMessageBox.StandardButton.Ok: return
            if not _restore:
                self.store.save(self.account,c['username'],c['nick_name'] or '',self.avatars.get(c['username']),
                                self.name.text().strip() or None,self.pending_avatar)
            report=self.offline.probe(context,c['username'],None if _restore else self.pending_avatar,_invalidate=_restore)
            text=json.dumps(report,ensure_ascii=False,indent=2)
            path=self.store.path.parent/('compatibility-'+uuid.uuid4().hex+'.json')
            path.write_text(text,encoding='utf-8')
            box=QMessageBox(self);box.setWindowTitle('副本兼容性检查通过')
            box.setText('加密副本写入、读回和其他好友记录校验通过；原始头像库和日志均未改动。\n报告不含好友 ID、昵称、头像、数据路径或密钥。默认不写原库；可选择仅本次试验操作，仍需确认客户端显示。')
            box.setDetailedText(text)
            copy=box.addButton('复制检查结果',QMessageBox.ButtonRole.ActionRole)
            trial=box.addButton('仅本次试验恢复' if _restore else '仅本次试验应用',QMessageBox.ButtonRole.AcceptRole)
            close=box.addButton('关闭',QMessageBox.ButtonRole.RejectRole)
            box.setDefaultButton(close);box.exec()
            if box.clickedButton()==trial:
                if _restore: self.restore(candidate_trial=True)
                else: self.apply(candidate_trial=True)
                return
            if box.clickedButton()==copy: QApplication.clipboard().setText(text)
            self.status.setText('副本兼容性检查通过，报告已保存。本次未改动原库；要测试实际显示，可重新检查后选择仅本次试验应用。')
        except Exception as exc:
            self.operation_error(exc)

    def reapply_expired(self):
        if not self.expired: return
        if not self.confirm_write('将复用已保存的头像重新应用。新版会逐个先检查副本，再仅本次试验写回。'): return
        records={(r['account'],r['friend']):r for r in self.store.rows()}
        done=0
        try:
            for account,friend in self.expired:
                context=self.contexts.get(account)
                if context is None: raise OfflineError('部分账号尚未在本次运行读取，无法取得密钥')
                record=records[(account,friend)]
                candidate=context.detected_version not in WRITE_VERSIONS
                if candidate: self.offline.probe(context,friend,record['desired_avatar'])
                backup=self.offline.apply(context,friend,record['desired_avatar'],_candidate_trial=candidate)
                self.store.record_applied(account,friend,backup);done+=1
            self.expired=[];self.render()
            self.status.setText(f'{done} 项头像库记录已重新应用并校验；请启动微信检查客户端显示。')
        except Exception as exc:
            self.status.setText(f'已重新应用 {done} 项，其余未完成。')
            self.operation_error(exc)

    def restore(self, candidate_trial=False):
        c=self.selected()
        if not c: return
        try:
            context=self.context()
            if context.detected_version not in WRITE_VERSIONS and not candidate_trial:
                self.probe(_restore=True);return
            if not self.confirm_write('将清除指定好友在头像库中的缓存记录，让微信尝试重新获取当前头像。不会写回可能过时的原图；工具配置保留。'): return
            backup=self.offline.invalidate(context,c['username'],_candidate_trial=candidate_trial)
            self.store.record_invalidated(self.account,c['username'],backup)
            self.render();self.status.setText('指定头像缓存记录已清除。请重启微信等待重新获取头像；其他缓存显示尚未验证。')
        except Exception as exc:
            self.operation_error(exc)

    def recover(self):
        try:
            context=self.context()
            if not self.confirm_write('将检查上次未完成的写入并尝试故障回滚。此操作仅用于中断恢复，不是恢复好友当前头像。'): return
            count=self.offline.recover(context.account,context=context)
            self.status.setText(f'故障恢复完成：{count} 项。')
        except Exception as exc:
            self.operation_error(exc)

    def operation_error(self,exc):
        # SQLCipher exceptions can contain internal paths. Never expose raw exceptions.
        message=str(exc) if isinstance(exc,(OfflineError,CompatibilityError,CipherError,VersionError)) else '操作失败。请保持微信关闭，保留工具备份，点击“故障恢复”。未确认成功的操作不会显示为有效。'
        QMessageBox.warning(self,'操作未完成',message)

    def clear(self):
        c = self.selected()
        if not c: return
        if QMessageBox.question(self,'仅清除配置','这不会恢复或修改微信数据。是否删除该好友的本地配置和保存的头像副本？') == QMessageBox.StandardButton.Yes:
            self.store.clear_config(self.account,c['username']); self.render()

    def closeEvent(self, event):
        if self.worker and self.worker.isRunning():
            self.status.setText('读取正在进行，请等待完成后退出，避免遗留解密临时文件。')
            event.ignore(); return
        event.accept()

def main():
    app = QApplication([])
    base = Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'WeChatLocalProfileEditor'
    window = Window(Store(base)); window.show()
    return app.exec()

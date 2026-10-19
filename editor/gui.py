import os
from pathlib import Path
from PySide6.QtCore import QThread, Signal, Qt
from PySide6.QtGui import QPixmap, QIcon
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
    QHBoxLayout, QLabel, QPushButton, QLineEdit, QComboBox, QListWidget,
    QListWidgetItem, QFileDialog, QMessageBox)
from .core import Store, normalize_image, Writer, UnsupportedWrite, State
from .reader import Session, auto_detect_db_dir, list_accounts
from .windows import discover

class Loader(QThread):
    ready = Signal(object)
    failed = Signal(str)
    def __init__(self, root, account):
        super().__init__()
        self.root, self.account = root, account
    def run(self):
        session = None
        try:
            discover()  # Fail closed on unsupported versions.
            session = Session(self.root, self.account)
            contacts = session.reader.contacts()
            avatars = {}
            for contact in contacts:
                try:
                    avatars[contact['username']] = session.reader.avatar(contact['username'])
                except Exception:
                    avatars[contact['username']] = None
            self.ready.emit((self.account, contacts, avatars))
        except Exception:
            # Do not expose upstream exceptions containing sensitive paths/data.
            self.failed.emit('读取失败：请确认微信版本为 4.1.13.65、已登录、账号目录正确且进程权限一致。尚未完成实机适配。')
        finally:
            if session:
                session.close()

class Window(QMainWindow):
    def __init__(self, store):
        super().__init__()
        self.store, self.contacts, self.avatars, self.account = store, [], {}, None
        self.worker = None
        self.pending_avatar = None
        self.setWindowTitle('微信本地身份工具 · 只读验证版（不可修改微信）')
        self.resize(1020, 640)
        box = QWidget(); self.setCentralWidget(box)
        layout = QVBoxLayout(box)
        self.info = QLabel('正在识别微信环境……'); layout.addWidget(self.info)
        layout.addWidget(QLabel('尚未验证原版客户端替换。本版本仅读取联系人、保存配置；应用和恢复将被拒绝。'))
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
        self.name = QLineEdit(); self.name.setPlaceholderText('自定义本地昵称（仅保存意图，尚不能应用）'); right.addWidget(self.name)
        self.button(right, '选择新头像', self.choose_image)
        self.button(right, '保存配置（不会修改微信）', self.save)
        self.button(right, '应用至微信', self.apply)
        right.addStretch()
        bottom = QHBoxLayout(); layout.addLayout(bottom)
        self.button(bottom, '重新检查配置', self.check)
        self.button(bottom, '重新应用', self.apply)
        self.button(bottom, '清除选中配置', self.clear)
        self.button(bottom, '恢复微信数据', self.restore)
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
        self.account, self.contacts, self.avatars = result
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
            self.render(); self.status.setText('配置已持久保存；未修改微信，状态为无法验证。')
        except ValueError as exc:
            QMessageBox.warning(self,'无法保存',str(exc))

    def check(self):
        # Cached snapshots are intentionally not treated as current live evidence.
        results = self.store.verify_all(lambda account,friend: None)
        self.status.setText(f'已检查 {len(results)} 项配置：当前无已验证的实时资源读取后端，状态均为无法验证。')

    def apply(self):
        try: Writer().apply()
        except UnsupportedWrite as exc: QMessageBox.warning(self,'功能尚未适配',str(exc))

    def restore(self):
        try: Writer().restore()
        except UnsupportedWrite as exc: QMessageBox.warning(self,'无法恢复',str(exc))

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

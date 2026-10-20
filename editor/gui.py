import os
import json
from pathlib import Path
from PySide6.QtCore import QThread, Signal, Qt
from PySide6.QtGui import QPixmap, QIcon
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
    QHBoxLayout, QLabel, QPushButton, QLineEdit, QComboBox, QListWidget,
    QListWidgetItem, QFileDialog, QMessageBox, QScrollArea)
from .core import Store, normalize_image, Writer, UnsupportedWrite, State
from .reader import Session, auto_detect_db_dir, list_accounts
from .windows import discover, WRITE_VERSIONS, READ_VERSIONS, VersionError, WechatRunningError, HELPER_PROCESSES, close_residual_helpers
from .offline import OfflineAvatar, OfflineError
from .compatibility import CompatibilityError
from .cipher_stage import CipherError
from .diagnostics import login_report
from .errors import remark_failure
from .observe import read_current, read_details
from .trace import LoginTrace, Timeline, RemarkTimeline, wechat_running
from .local_remark import OfflineRemark, payload as remark_payload
from .observe import read_remark, read_remarks

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
            self.ready.emit((self.account, contacts, avatars, session.write_context(executable, detected_version),
                             session.write_context(executable, detected_version, "contact.db")))
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
        self.trace_worker = None
        self.name_worker = None
        self.name_contexts = {}
        self.contexts = {}
        self.offline = OfflineAvatar(store.path.parent / 'backups')
        self.remarks = OfflineRemark(store.path.parent / 'remark-backups')
        self.expired = []
        self.pending_avatar = None
        self.setWindowTitle('微信本地身份工具 · 实验性离线头像替换 · 头像与本机备注缓存试验版')
        self.resize(1020, 640)
        box = QWidget(); self.setCentralWidget(box)
        layout = QVBoxLayout(box)
        self.info = QLabel('正在识别微信环境……'); layout.addWidget(self.info)
        layout.addWidget(QLabel('4.1.13.65：实验性写回；4.1.15.56 / 4.1.15.65：先检查副本，通过后可仅本次试验写入。本机备注覆盖为试验功能，手机备注不变尚未验收。'))
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
        right_widget=QWidget(); right=QVBoxLayout(right_widget)
        right_scroll=QScrollArea(); right_scroll.setWidgetResizable(True); right_scroll.setWidget(right_widget)
        body.addWidget(right_scroll,2)
        self.preview = QLabel('原头像'); self.preview.setMinimumSize(128,128); right.addWidget(self.preview)
        self.identity = QLabel('请选择好友'); self.identity.setWordWrap(True); right.addWidget(self.identity)
        self.desired_preview = QLabel('未选择自定义头像'); right.addWidget(self.desired_preview)
        right.addWidget(QLabel('本机备注覆盖（试验）'))
        self.name = QLineEdit(); self.name.setPlaceholderText('电脑显示文字 B；手机原备注 A 应保持不变（待验收）'); right.addWidget(self.name)
        self.remark_button = self.button(right, '本机备注覆盖试验', self.remark_trial)
        self.button(right, '恢复电脑原备注缓存', self.restore_remark)
        self.button(right, '清除本机备注配置（不恢复微信）', self.clear_remark)
        self.button(right, '备注故障恢复', self.recover_remark)
        right.addWidget(QLabel('头像替换'))
        self.button(right, '选择新头像', self.choose_image)
        self.button(right, '保存头像配置（不会修改微信）', self.save)
        self.button(right, '应用头像至微信', self.apply)
        self.button(right, '新版兼容性检查（不写原库）', self.probe)
        self.button(right, '登录后头像诊断（只读）', self.diagnose_login)
        self.trace_button = self.button(right, '开始一键诊断（保留字段）', self.guided_trace)
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
        return b

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
        if len(result)>4 and result[4] is not None:
            self.name_contexts[self.account]=result[4]
        self.render()
        self.status.setText(f'读取 {len(self.contacts)} 个联系人；头像来自解密快照，不能证明微信显示结果。')
        self.check()

    def render(self):
        records = {(r['account'],r['friend']):r for r in self.store.rows()}
        remark_records={(r['account'],r['friend']):r for r in self.store.remark_rows()}
        query = self.search.text().casefold()
        self.items.clear()
        contacts = sorted(self.contacts, key=lambda c: (self.account,c['username']) not in records and (self.account,c['username']) not in remark_records)
        for c in contacts:
            if query not in ' '.join(str(v or '') for v in c.values()).casefold(): continue
            r = records.get((self.account,c['username']))
            text = f"{c['remark'] or c['nick_name'] or c['username']}\n{c['username']} · " + (f"已配置 / {r['state']}" if r else '未修改')
            remark=remark_records.get((self.account,c['username']))
            if remark: text+=f" / 本机备注试验：{remark['state']}"
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
        for row in self.store.remark_rows():
            if (row['account'],row['friend']) == (self.account,c['username']):
                self.name.setText(row['desired_name'])
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
                            None,self.pending_avatar)
            self.render(); self.status.setText('配置已持久保存；此步骤未写入微信。')
        except ValueError as exc:
            QMessageBox.warning(self,'无法保存',str(exc))

    def remark_trial(self, restore=False, _confirmed=False):
        if self.name_worker and self.name_worker.isRunning():
            self.name_worker.stop()
            self.remark_button.setEnabled(False)
            self.remark_button.setText('正在结束备注诊断……')
            return
        c=self.selected()
        if not c: return
        if not c.get('remark'):
            QMessageBox.information(self,'没有原备注','请选择已经设置手机备注的测试好友。')
            return
        context=None;stage='configuration';backend_entered=False
        try:
            row=next((r for r in self.store.remark_rows() if (r['account'],r['friend'])==(self.account,c['username'])),None)
            stage='select_context'
            context=self.context('remark')
            if restore and row is None: raise OfflineError('没有保存的原备注缓存')
            name=row['original_name'] if restore else self.name.text().strip()
            stage='validate_input'
            data=remark_payload(name,restore=restore)
            if not _confirmed and QMessageBox.question(self,'本机备注覆盖试验 · 不同步尚未验证',
                '请先用测试联系人验收：目标为电脑显示 B、手机保留 A。尚未验证手机备注绝不会变化。\n'
                '本次只修改本机数据库 remark 缓存，保留原昵称、身份 ID、同步标志和其他记录；不调用官方备注功能。\n'
                '请完全退出微信（含托盘），再点确定。工具会备份、校验并试写一次。\n'
                '随后登录微信，检查名称及手机是否变化，最后点“结束备注诊断并复制报告”。',
                QMessageBox.StandardButton.Ok|QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel)!=QMessageBox.StandardButton.Ok: return
            stage='copy_probe';backend_entered=True
            self.remarks.probe(context,c['username'],data)
            stage='read_original_remark'
            original=self.remarks.original_name(context,c['username'],data)
            stage='save_configuration'
            if not restore: self.store.save_remark(self.account,c['username'],original,name)
            stage='writeback'
            self.remarks.apply(context,c['username'],data,_candidate_trial=True)
            stage='save_write_history'
            self.store.record_remark(self.account,c['username'],applied=not restore,restored=restore)
            stage='start_sampling'
            timeline=RemarkTimeline(context.detected_version,name)
            try: current=read_remark(context,c['username'])
            except Exception: current=None
            timeline.add(0,current,wechat_running())
            worker=LoginTrace(context,c['username'],timeline,reader=read_remark)
            self.name_worker=worker
            worker.progress.connect(lambda seconds:self.status.setText(
                f'备注采集 {seconds} 秒：请登录微信检查实际名称及手机，再点击结束。缓存一致不代表纯本地。'))
            worker.finished.connect(self.remark_finished)
            self.remark_button.setText('结束备注诊断并复制报告')
            self.status.setText('本机备注缓存已试写，正在只读采集。请登录微信检查显示及手机，再点结束。')
            worker.start()
        except Exception as exc:
            self.remark_error(exc,stage,context,self.remarks if backend_entered else None,
                              retry=lambda:self.remark_trial(restore=restore,_confirmed=True))

    def remark_error(self,exc,stage,context,service,retry=None):
        report=remark_failure(exc,version=getattr(context,'detected_version',None),
                              gui_stage=stage,service=service)
        text=json.dumps(report,ensure_ascii=False,indent=2)
        QApplication.clipboard().setText(text)
        try: (self.store.path.parent/'desktop-remark-error.json').write_text(text,encoding='utf-8')
        except OSError: pass
        outcome=report['source_write_outcome']
        if report['error_category']=='pending_previous_operation':
            result='本次未写入。发现此前未完成操作，请保持微信关闭，点击“备注故障恢复”。'
        elif report['error_category']=='wechat_process_still_running':
            result='进程检查未通过，本次未写入微信数据。主窗口关闭后，附属进程仍可能存在。'
        elif outcome in ('not_entered','not_started','not_written'):
            result='本次尚未写入微信数据；这个错误本身不代表微信仍在运行，也不代表需要故障恢复。'
        elif outcome=='rolled_back':
            result='本次写入失败，已回滚到操作前的数据。'
        elif outcome=='written_verified':
            result='数据库已写入并校验；失败发生在后续步骤。请勿反复试写。'
        else:
            result='源文件状态尚未确认，请保持微信关闭并使用“备注故障恢复”。'
        labels={'unsupported_sqlite_tokenizer':'联系人库使用当前引擎不支持的分词器',
                'unsupported_sqlite_collation':'联系人库使用当前引擎不支持的排序规则',
                'unsupported_sqlite_module':'联系人库使用当前引擎不支持的扩展表',
                'wechat_process_still_running':'检测到微信相关进程',
                'wechat_process_status_unknown':'无法核实微信进程状态',
                'exclusive_file_lock_failed':'无法取得目标文件独占权限',
                'filesystem_permission_denied':'文件访问被拒绝',
                'invalid_desktop_remark':'请输入 1 至 64 个字符，不含换行或控制字符',
                'helper_identity_or_installation_mismatch':'组件身份或安装目录不匹配，未自动结束',
                'process_identity_changed':'进程身份已变化，未继续处理',
                'helper_termination_permission_denied':'无权结束组件，请确认工具与微信权限一致',
                'helper_identity_unverifiable':'无权核实组件身份，未自动结束',
                'process_list_changed':'微信进程名单已变化，请重新确认',
                'helper_exit_timeout':'组件未在限时内退出，操作已停止',
                'unclassified_error':'尚未分类的异常'}
        category=labels.get(report['error_category'],report['error_category'])
        box=QMessageBox(self)
        box.setWindowTitle('备注操作未完成 · 脱敏报告已复制')
        box.setIcon(QMessageBox.Icon.Warning)
        box.setText(f'{result}\n阶段：{stage} / {report["backend_stage"]}\n原因：{category}（{report["exception_type"]}）\n\n错误报告已自动复制，回到对话直接粘贴即可。')
        box.setDetailedText(text)
        copy=box.addButton('再次复制错误报告',QMessageBox.ButtonRole.ActionRole)
        close=box.addButton('关闭',QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(close);box.setEscapeButton(close)
        cleanup=again=None
        blockers=exc.blocking_processes if isinstance(exc,WechatRunningError) else ()
        if blockers:
            listing='\n'.join(f'{p.name}（PID {p.pid}）' for p in blockers[:16])
            box.setText(box.text()+'\n\n检测到：\n'+listing)
            if retry and report['source_write_outcome'] in ('not_entered','not_started','not_written'):
                again=box.addButton('重新检查并重试',QMessageBox.ButtonRole.ActionRole)
                if len(blockers)<=16 and all(p.name in HELPER_PROCESSES and p.created is not None for p in blockers):
                    cleanup=box.addButton('结束残留组件并重试',QMessageBox.ButtonRole.DestructiveRole)
                    box.setText(box.text()+'\n\n仅检测到附属组件。选择“结束残留组件并重试”会强制结束列出的组件，未保存的小程序或网页内容可能丢失。不会强制结束微信主程序；仅处理本次安装目录内的组件。')
        box.setDefaultButton(close);box.setEscapeButton(close)
        box.exec()
        clicked=box.clickedButton()
        if clicked==copy:QApplication.clipboard().setText(text)
        elif cleanup is not None and clicked==cleanup:
            try:
                close_residual_helpers(blockers,context.executable.parent)
            except Exception as cleanup_error:
                self.remark_error(cleanup_error,'close_residual_helpers',context,None,retry=retry)
                return
            retry()
        elif again is not None and clicked==again:
            retry()

    def remark_finished(self):
        worker=self.name_worker;self.name_worker=None
        self.remark_button.setEnabled(True)
        self.remark_button.setText('本机备注覆盖试验')
        if not worker or worker.result is None:
            self.status.setText('备注采集未生成报告。');return
        text=json.dumps(worker.result,ensure_ascii=False,indent=2)
        QApplication.clipboard().setText(text)
        try: (self.store.path.parent/'desktop-remark-diagnostic.json').write_text(text,encoding='utf-8')
        except OSError: pass
        self.status.setText('备注脱敏报告已复制；未验证客户端显示和远端不同步。')
        QMessageBox.information(self,'备注诊断结束','报告已复制，直接粘贴发来即可。\n请另外说明电脑名称是否变化、重启是否保留，以及手机名称是否变化。报告不包含任何实际备注。')

    def restore_remark(self):
        self.remark_trial(restore=True)

    def clear_remark(self):
        c=self.selected()
        if c and QMessageBox.question(self,'仅清除本机备注配置','不会恢复微信缓存。确定清除这个好友的本机备注配置？')==QMessageBox.StandardButton.Yes:
            self.store.clear_remark(self.account,c['username']);self.name.clear()

    def recover_remark(self):
        try:
            context=self.context('remark')
            if not self.confirm_write('将恢复此账号未完成的备注数据库写入；仅处理能识别的备份状态。'):return
            count=self.remarks.recover(self.account,context=context)
            self.status.setText(f'已恢复 {count} 项未完成备注操作。客户端及远端状态仍需检查。')
        except Exception as exc:self.operation_error(exc)

    def guided_trace(self):
        if self.trace_worker and self.trace_worker.isRunning():
            self.trace_button.setEnabled(False)
            self.trace_button.setText('正在结束诊断……')
            self.trace_worker.stop()
            return
        c = self.selected()
        if not c: return
        row = next((r for r in self.store.rows() if r['account'] == self.account
                    and r['friend'] == c['username']), None)
        if not row or not row['desired_avatar']:
            QMessageBox.information(self, '请选择测试好友', '请选择之前保存过自定义头像的好友，无须重新选图片。')
            return
        try:
            context = self.context()
            if QMessageBox.question(self, '一键诊断',
                '请保持本工具打开，完全退出微信（含托盘），然后点“确定”。\n'
                '工具将自动备份、检查并试写已保存头像一次。本次仅替换图片，保留原有 md5 和时间等字段，再开始只读采集。\n'
                '看到“正在诊断”后，登录微信并打开该好友聊天窗口，再点“结束诊断并复制报告”。\n'
                '最多采集 3 分钟，不安装服务、不上传数据；本次仍用于定位，尚非修复。',
                QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel) != QMessageBox.StandardButton.Ok: return
            self.offline.probe(context, c['username'], row['desired_avatar'])
            backup = self.offline.apply(context, c['username'], row['desired_avatar'], _candidate_trial=True)
            self.store.record_applied(self.account, c['username'], backup)
            row = dict(row, last_applied=True)
            timeline = Timeline(row, context.detected_version)
            try:
                details = read_details(context, c['username'])
            except Exception:
                details = None
            timeline.add(0, details, wechat_running())
            worker = LoginTrace(context, c['username'], timeline)
            self.trace_worker = worker
            worker.progress.connect(lambda seconds: self.status.setText(
                f'正在诊断（{seconds} 秒）：请登录微信并打开该好友聊天窗口，随后点击“结束诊断并复制报告”。'))
            worker.finished.connect(self.trace_finished)
            self.trace_button.setText('结束诊断并复制报告')
            self.status.setText('正在诊断：请登录微信，打开该好友聊天窗口，再点“结束诊断并复制报告”。')
            worker.start()
        except Exception as exc:
            self.operation_error(exc)

    def trace_finished(self):
        worker = self.trace_worker
        self.trace_button.setEnabled(True)
        self.trace_button.setText('开始一键诊断（保留字段）')
        self.trace_worker = None
        if not worker or worker.result is None:
            self.status.setText('诊断未完成，未生成报告。')
            return
        text = json.dumps(worker.result, ensure_ascii=False, indent=2)
        QApplication.clipboard().setText(text)
        report_path = self.store.path.parent / 'login-diagnostic.json'
        try:
            report_path.write_text(text, encoding='utf-8')
            saved = f'报告同时保存在：{report_path}'
        except OSError:
            saved = '报告文件保存失败，但已复制到剪贴板。'
        self.status.setText('诊断已结束，采集线程已退出。报告已复制，可直接粘贴发来。')
        QMessageBox.information(self, '诊断已结束',
            '脱敏报告已复制到剪贴板，回到对话直接粘贴即可。\n' + saved +
            '\n报告不含好友身份、图片、密钥或微信数据路径。实际显示替换尚未修复。')

    def diagnose_login(self):
        c = self.selected()
        if not c: return
        row = next((r for r in self.store.rows() if r['account'] == self.account
                    and r['friend'] == c['username']), None)
        if not row:
            QMessageBox.information(self, '尚无配置', '请选中之前已保存替换配置的好友。')
            return
        try:
            context = self.context()
            try:
                current = read_current(context, [c['username']])[c['username']]
            except Exception:
                current = None
            report = login_report(row, current, context.detected_version)
            text = json.dumps(report, ensure_ascii=False, indent=2)
            box = QMessageBox(self)
            box.setWindowTitle('登录后头像诊断 · 未修改微信')
            box.setText(report['scope'])
            box.setDetailedText(text)
            copy = box.addButton('复制脱敏报告', QMessageBox.ButtonRole.ActionRole)
            box.addButton('关闭', QMessageBox.ButtonRole.RejectRole)
            box.exec()
            if box.clickedButton() == copy:
                QApplication.clipboard().setText(text)
        except Exception as exc:
            self.operation_error(exc)

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
        remark_rows=self.store.remark_rows();remark_observations={}
        for account,context in self.name_contexts.items():
            friends=[r['friend'] for r in remark_rows if r['account']==account]
            if not friends: continue
            try: remark_observations[account]=read_remarks(context,friends)
            except Exception: pass
        for row in remark_rows:
            current=remark_observations.get(row['account'],{}).get(row['friend'])
            self.store.record_remark(row['account'],row['friend'],observed=current)
        self.expired=[(a,f) for a,f,state,scope in results if state==State.INVALID]
        self.status.setText(f'检查 {len(results)} 项配置，发现 {len(self.expired)} 项本地替换已失效。数据库一致也不能证明客户端显示一致。')
        if remark_rows:
            changed=sum(r['state']==State.INVALID for r in self.store.remark_rows())
            self.status.setText(self.status.text()+f' 另有 {len(remark_rows)} 项本机备注试验，{changed} 项缓存变化，手机不变尚未验收。')
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

    def context(self, kind='avatar'):
        if self.name_worker and self.name_worker.isRunning():
            raise OfflineError('正在采集昵称，请先点击结束备注诊断')
        if self.trace_worker and self.trace_worker.isRunning():
            raise OfflineError('正在诊断，请先点击“结束诊断并复制报告”，避免其他操作干扰结果')
        context=(self.name_contexts if kind=='remark' else self.contexts).get(self.account)
        if context is None:
            raise OfflineError('请先登录微信并读取此账号联系人，以取得本次运行所需的内存密钥')
        if self.directory.text() and Path(self.directory.text()).resolve()!=context.account_root.parent.resolve():
            raise OfflineError('数据目录已变化，请重新读取联系人')
        if self.accounts.currentData()!=self.account:
            raise OfflineError('所选账号已变化，请重新读取联系人')
        return context

    def confirm_write(self,text):
        if self.name_worker and self.name_worker.isRunning():
            self.status.setText('请先结束备注诊断，再执行其他写入。')
            return False
        if self.trace_worker and self.trace_worker.isRunning():
            self.status.setText('请先结束诊断，再进行其他写入操作。')
            return False
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
            self.store.save(self.account,c['username'],c['nick_name'] or '',self.avatars.get(c['username']),None,avatar)
            backup=self.offline.apply(context,c['username'],avatar,_candidate_trial=candidate_trial)
            self.store.record_applied(self.account,c['username'],backup)
            self.render()
            self.status.setText('加密头像库已写入并校验，备份已保存。现在可启动微信检查会话列表、聊天窗口和资料页；客户端生效尚待确认。')
            QMessageBox.information(self,'数据库写入完成','仅完成头像数据库写入，实际替换尚未成功验收。请登录微信检查头像；如未变化，保持工具打开，选中该好友点击“登录后头像诊断（只读）”。不要反复写入。')
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
                                None,self.pending_avatar)
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
        if self.name_worker and self.name_worker.isRunning():
            self.name_worker.stop()
            self.status.setText('正在停止备注采集，报告复制后请再关闭。')
            event.ignore(); return
        if self.trace_worker and self.trace_worker.isRunning():
            self.trace_worker.stop()
            self.status.setText('正在停止诊断，请稍后再关闭。报告将自动复制。')
            event.ignore(); return
        if self.worker and self.worker.isRunning():
            self.status.setText('读取正在进行，请等待完成后退出，避免遗留解密临时文件。')
            event.ignore(); return
        event.accept()

def main():
    app = QApplication([])
    base = Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'WeChatLocalProfileEditor'
    window = Window(Store(base)); window.show()
    return app.exec()

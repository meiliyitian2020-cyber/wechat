import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtWidgets import QApplication
from editor.core import Store
from editor.gui import Window

def test_chinese_ui_persistence_and_no_success_claim(tmp_path, monkeypatch):
    def unavailable():
        raise RuntimeError('测试环境：不访问真实微信')
    monkeypatch.setattr('editor.gui.discover', unavailable)
    app = QApplication.instance() or QApplication([])
    store=Store(tmp_path)
    store.save('a','f','真实昵称',None,'自定义昵称',None)
    w=Window(store)
    w.loaded(('a',[{'username':'f','nick_name':'真实昵称','remark':'备注'}],{}))
    assert w.items.count()==1
    w.items.setCurrentRow(0)
    assert w.name.text()=='自定义昵称'
    w.search.setText('备注')
    assert w.items.count()==1
    w.search.setText('找不到')
    assert w.items.count()==0
    assert store.rows()[0]['state']=='无法验证'
    assert w.close()
    app.processEvents()

def test_loader_explains_version_error_without_generic_permission_advice(monkeypatch):
    from editor.gui import Loader
    from editor.windows import VersionError
    def unsupported(): raise VersionError('版本 4.1.15.57 尚未适配，禁止读取和修改')
    monkeypatch.setattr('editor.gui.discover',unsupported)
    messages=[];loader=Loader('unused','unused')
    loader.failed.connect(messages.append);loader.run()
    assert len(messages)==1
    assert '4.1.15.57' in messages[0]
    assert '版本识别' in messages[0]
    assert '权限一致' not in messages[0]


def test_guided_diagnostic_reuses_saved_photo_and_copies_report(tmp_path, monkeypatch):
    import json
    from types import SimpleNamespace
    from editor.trace import LoginTrace
    from PySide6.QtWidgets import QMessageBox
    monkeypatch.setattr('editor.gui.discover', lambda: (_ for _ in ()).throw(RuntimeError('test')))
    monkeypatch.setattr(QMessageBox, 'question', lambda *args: QMessageBox.StandardButton.Ok)
    monkeypatch.setattr(QMessageBox, 'information', lambda *args: QMessageBox.StandardButton.Ok)
    app = QApplication.instance() or QApplication([])
    store = Store(tmp_path)
    store.save('a', 'f', 'name', b'private-original', None, b'private-custom')
    w = Window(store)
    w.loaded(('a', [{'username':'f', 'nick_name':'name', 'remark':''}], {}))
    w.items.setCurrentRow(0)
    context = SimpleNamespace(detected_version='4.1.15.56')
    monkeypatch.setattr(w, 'context', lambda: context)
    actions = []
    monkeypatch.setattr(w.offline, 'probe', lambda ctx, friend, image: actions.append(('probe', friend, image)))
    def apply(ctx, friend, image, **kwargs):
        actions.append(('apply', friend, image)); return 'local-test-backup'
    monkeypatch.setattr(w.offline, 'apply', apply)
    observed = dict(image=b'private-custom', md5='', update_time=1)
    monkeypatch.setattr('editor.gui.read_details', lambda *args:observed)
    monkeypatch.setattr('editor.gui.wechat_running', lambda:False)
    monkeypatch.setattr('editor.gui.LoginTrace', lambda ctx, friend, timeline:LoginTrace(
        ctx, friend, timeline, reader=lambda *args:observed, process=lambda:True,
        interval=.005, duration=.02))
    w.guided_trace()
    worker = w.trace_worker
    assert worker is not None
    assert worker.wait(1000)
    app.processEvents()
    assert [action[0] for action in actions] == ['probe', 'apply']
    assert actions[-1][2] == b'private-custom'
    report = json.loads(app.clipboard().text())
    assert report['custom_bytes_observed'] is True
    assert report['source_written_by_sampler'] is False
    assert (tmp_path / 'login-diagnostic.json').exists()
    assert w.trace_worker is None
    assert w.trace_button.text() == '开始一键诊断（保留字段）'
    assert w.close()


def test_desktop_remark_a_b_flow_reuses_config_and_preserves_avatar(tmp_path, monkeypatch):
    import json
    from types import SimpleNamespace
    from PySide6.QtWidgets import QMessageBox
    from editor.trace import LoginTrace
    from editor.local_remark import decode
    monkeypatch.setattr('editor.gui.discover',lambda:(_ for _ in ()).throw(RuntimeError('synthetic')))
    monkeypatch.setattr(QMessageBox,'question',lambda *args:QMessageBox.StandardButton.Ok)
    monkeypatch.setattr(QMessageBox,'information',lambda *args:QMessageBox.StandardButton.Ok)
    app=QApplication.instance() or QApplication([])
    store=Store(tmp_path)
    store.save('a','f','CCCCC',b'original-avatar',None,b'custom-avatar')
    w=Window(store)
    w.loaded(('a',[{'username':'f','nick_name':'CCCCC','remark':'A'}],{}))
    w.items.setCurrentRow(0);w.name.setText('B')
    context=SimpleNamespace(detected_version='4.1.15.56')
    monkeypatch.setattr(w,'context',lambda kind='avatar':context)
    operations=[]
    monkeypatch.setattr(w.remarks,'probe',lambda ctx,friend,data:operations.append(('probe',decode(data))))
    monkeypatch.setattr(w.remarks,'original_name',lambda *args:'A')
    monkeypatch.setattr(w.remarks,'apply',lambda ctx,friend,data,**kwargs:operations.append(('apply',decode(data))))
    observed={'name':'B','nickname':'CCCCC','local_type':1}
    monkeypatch.setattr('editor.gui.read_remark',lambda *args:observed)
    monkeypatch.setattr('editor.gui.wechat_running',lambda:False)
    monkeypatch.setattr('editor.gui.LoginTrace',lambda ctx,friend,timeline,**kwargs:LoginTrace(
        ctx,friend,timeline,reader=lambda *args:observed,process=lambda:True,interval=.01,duration=.05))
    w.remark_trial()
    assert w.name_worker is not None
    assert w.name_worker.wait(1000);app.processEvents()
    assert operations==[('probe','B'),('apply','B')]
    row=store.remark_rows()[0]
    assert row['original_name']=='A' and row['desired_name']=='B' and row['last_applied']
    assert store.rows()[0]['desired_avatar']==b'custom-avatar'
    report=json.loads(app.clipboard().text())
    assert report['operation']=='desktop_remark_guided_login_timeline'
    assert report['remote_non_sync_verified'] is False
    assert report['custom_remark_observed'] is True
    assert (tmp_path/'desktop-remark-diagnostic.json').exists()
    assert w.name_worker is None
    w.select()
    assert w.name.text()=='B'
    assert w.close()


def test_remark_probe_error_copies_redacted_report_without_blame_or_recovery_claim(tmp_path,monkeypatch):
    import json
    from types import SimpleNamespace
    from PySide6.QtWidgets import QMessageBox
    monkeypatch.setattr('editor.gui.discover',lambda:(_ for _ in ()).throw(RuntimeError('synthetic')))
    monkeypatch.setattr(QMessageBox,'question',lambda *args:QMessageBox.StandardButton.Ok)
    monkeypatch.setattr(QMessageBox,'exec',lambda *args:0)
    app=QApplication.instance() or QApplication([])
    w=Window(Store(tmp_path));w.loaded(('a',[{'username':'f','nick_name':'CCCCC','remark':'A'}],{}))
    w.items.setCurrentRow(0);w.name.setText('B')
    monkeypatch.setattr(w,'context',lambda *args:SimpleNamespace(detected_version='4.1.15.56'))
    def fail(*args):
        w.remarks.phase('remark_other_tables_digest_before')
        w.remarks.last_write_outcome='not_written'
        raise RuntimeError('no such tokenizer: private-name private-key private-path')
    monkeypatch.setattr(w.remarks,'probe',fail)
    monkeypatch.setattr(w,'operation_error',lambda *args:(_ for _ in ()).throw(AssertionError('generic error dialog used')))
    w.remark_trial()
    report=json.loads(app.clipboard().text())
    assert report['operation']=='desktop_remark_failure'
    assert report['gui_stage']=='copy_probe'
    assert report['error_category']=='unsupported_sqlite_tokenizer'
    assert report['source_write_started'] is False
    assert report['source_unchanged_by_this_operation'] is True
    assert 'private-' not in json.dumps(report)
    assert (tmp_path/'desktop-remark-error.json').exists()
    assert w.close()


def test_remark_error_after_verified_write_does_not_claim_source_untouched(tmp_path,monkeypatch):
    import json
    from types import SimpleNamespace
    from PySide6.QtWidgets import QMessageBox
    monkeypatch.setattr('editor.gui.discover',lambda:(_ for _ in ()).throw(RuntimeError('synthetic')))
    monkeypatch.setattr(QMessageBox,'question',lambda *args:QMessageBox.StandardButton.Ok)
    monkeypatch.setattr(QMessageBox,'exec',lambda *args:0)
    app=QApplication.instance() or QApplication([])
    w=Window(Store(tmp_path));w.loaded(('a',[{'username':'f','nick_name':'CCCCC','remark':'A'}],{}))
    w.items.setCurrentRow(0);w.name.setText('B')
    monkeypatch.setattr(w,'context',lambda *args:SimpleNamespace(detected_version='4.1.15.56'))
    monkeypatch.setattr(w.remarks,'probe',lambda *args:None)
    monkeypatch.setattr(w.remarks,'original_name',lambda *args:'A')
    def applied(*args,**kwargs):
        w.remarks.last_write_outcome='written_verified';w.remarks.phase('apply_complete')
    monkeypatch.setattr(w.remarks,'apply',applied)
    monkeypatch.setattr(w.store,'record_remark',lambda *args,**kwargs:(_ for _ in ()).throw(OSError('private-store-path')))
    w.remark_trial()
    report=json.loads(app.clipboard().text())
    assert report['gui_stage']=='save_write_history'
    assert report['source_write_outcome']=='written_verified'
    assert report['source_write_started'] is True
    assert report['source_unchanged_by_this_operation'] is False
    assert 'private-store-path' not in json.dumps(report)
    assert w.close()


def test_helper_cleanup_requires_explicit_choice_then_retries_without_bypassing_probe(tmp_path,monkeypatch):
    from types import SimpleNamespace
    from PySide6.QtWidgets import QMessageBox
    from editor.windows import WechatRunningError, BlockingProcess
    from editor.trace import LoginTrace
    monkeypatch.setattr('editor.gui.discover',lambda:(_ for _ in ()).throw(RuntimeError('synthetic')))
    questions=[]
    monkeypatch.setattr(QMessageBox,'question',lambda *args:(questions.append(True) or QMessageBox.StandardButton.Ok))
    monkeypatch.setattr(QMessageBox,'information',lambda *args:QMessageBox.StandardButton.Ok)
    chosen=[]
    def choose(self):
        assert self.defaultButton().text()=='关闭'
        chosen.append(next(b for b in self.buttons() if b.text()=='结束残留组件并重试'))
        assert '未保存' in self.text()
        return 0
    monkeypatch.setattr(QMessageBox,'exec',choose)
    monkeypatch.setattr(QMessageBox,'clickedButton',lambda self:chosen[-1])
    app=QApplication.instance() or QApplication([])
    w=Window(Store(tmp_path));w.loaded(('a',[{'username':'f','nick_name':'CCCCC','remark':'A'}],{}))
    w.items.setCurrentRow(0);w.name.setText('B')
    context=SimpleNamespace(detected_version='4.1.15.56',executable=tmp_path/'installation'/'Weixin.exe')
    monkeypatch.setattr(w,'context',lambda *args:context)
    calls=[]
    def probe(*args):
        calls.append('probe');w.remarks.last_write_outcome='not_written'
        if calls==['probe']:raise WechatRunningError([BlockingProcess(123,'wechatappex.exe',1.5)])
    monkeypatch.setattr(w.remarks,'probe',probe)
    def cleaned(approved,installation):
        assert approved[0].pid==123 and installation==context.executable.parent
        calls.append('cleanup')
    monkeypatch.setattr('editor.gui.close_residual_helpers',cleaned)
    monkeypatch.setattr(w.remarks,'original_name',lambda *args:'A')
    monkeypatch.setattr(w.remarks,'apply',lambda *args,**kwargs:calls.append('apply'))
    current={'name':'B','nickname':'CCCCC','local_type':1}
    monkeypatch.setattr('editor.gui.read_remark',lambda *args:current)
    monkeypatch.setattr('editor.gui.wechat_running',lambda:False)
    monkeypatch.setattr('editor.gui.LoginTrace',lambda c,f,t,**kwargs:LoginTrace(
        c,f,t,reader=lambda *args:current,process=lambda:False,interval=.01,duration=.05))
    w.remark_trial()
    assert calls==['probe','cleanup','probe','apply']
    assert len(questions)==1
    assert w.name_worker.wait(1000);app.processEvents()
    assert w.close()


def test_main_process_never_offers_forced_cleanup(tmp_path,monkeypatch):
    from types import SimpleNamespace
    from PySide6.QtWidgets import QMessageBox
    from editor.windows import WechatRunningError, BlockingProcess
    monkeypatch.setattr('editor.gui.discover',lambda:(_ for _ in ()).throw(RuntimeError('synthetic')))
    chosen=[]
    def close(self):
        assert not any(b.text()=='结束残留组件并重试' for b in self.buttons())
        assert self.defaultButton().text()=='关闭'
        chosen.append(self.defaultButton());return 0
    monkeypatch.setattr(QMessageBox,'exec',close)
    monkeypatch.setattr(QMessageBox,'clickedButton',lambda self:chosen[-1])
    app=QApplication.instance() or QApplication([])
    w=Window(Store(tmp_path))
    error=WechatRunningError([BlockingProcess(123,'weixin.exe',1.5),BlockingProcess(124,'wechatappex.exe',1.5)])
    w.remark_error(error,'copy_probe',SimpleNamespace(detected_version='4.1.15.56'),None,
                   retry=lambda:(_ for _ in ()).throw(AssertionError('retry without user choice')))
    assert w.close()

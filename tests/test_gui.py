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

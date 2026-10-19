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

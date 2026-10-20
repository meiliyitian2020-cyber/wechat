from pathlib import Path
import json
import psutil
import pytest
from editor import windows as w
from editor.errors import safe_error


class Process:
    def __init__(self,pid,name,path,created=1.5):
        self.pid,self._name,self.path,self.created=pid,name,str(path),created
        self.alive=True;self.terminated=0;self.waits=[]
    def name(self):return self._name
    def create_time(self):return self.created
    def exe(self):return self.path
    def terminate(self):self.terminated+=1;self.alive=False
    def wait(self,timeout):self.waits.append(timeout)
    def kill(self):raise AssertionError('No fallback kill is allowed')


@pytest.fixture
def install(tmp_path,monkeypatch):
    monkeypatch.setattr(w,'_require_windows',lambda:None)
    root=tmp_path/'installation';root.mkdir()
    helper=root/'helper'/'WeChatAppEx.exe';helper.parent.mkdir();helper.write_bytes(b'synthetic')
    return root,helper


def test_guard_lists_all_main_and_helper_names_without_exporting_paths_or_times(install,monkeypatch):
    root,path=install
    processes=[Process(101,'WeChatAppEx.exe',path),Process(102,'Weixin.exe',root/'Weixin.exe')]
    monkeypatch.setattr(w.psutil,'process_iter',lambda:processes)
    with pytest.raises(w.WechatRunningError) as caught:w.ensure_wechat_stopped()
    result=safe_error(caught.value)
    assert result['blocking_processes']==[
        {'name':'wechatappex.exe','pid':101,'role':'helper'},
        {'name':'weixin.exe','pid':102,'role':'main'}]
    text=json.dumps(result)
    assert str(root) not in text and 'created' not in text
    assert result['error_category']=='wechat_process_still_running'


def test_explicit_cleanup_only_terminates_reviewed_helpers_in_current_install(install,monkeypatch):
    root,path=install
    processes=[Process(101,'WeChatAppEx.exe',path),Process(102,'WeChatAppEx.exe',path)]
    by_id={p.pid:p for p in processes}
    monkeypatch.setattr(w.psutil,'process_iter',lambda:[p for p in processes if p.alive])
    monkeypatch.setattr(w.psutil,'Process',lambda pid:by_id[pid])
    approved=w.blocking_processes()
    w.close_residual_helpers(approved,root)
    assert all(p.terminated==1 for p in processes)
    assert all(0<timeout<=5 for p in processes for timeout in p.waits)
    w.ensure_wechat_stopped()


def test_main_process_prevents_any_automatic_termination(install,monkeypatch):
    root,path=install
    helper=Process(101,'WeChatAppEx.exe',path);main=Process(102,'Weixin.exe',root/'Weixin.exe')
    monkeypatch.setattr(w.psutil,'process_iter',lambda:[helper,main])
    with pytest.raises(w.WechatRunningError):
        w.close_residual_helpers([w.BlockingProcess(101,'wechatappex.exe',1.5)],root)
    assert helper.terminated==0 and main.terminated==0


@pytest.mark.parametrize('problem',['other_install','reused_pid','unapproved_process'])
def test_all_targets_are_validated_before_any_termination(install,monkeypatch,problem):
    root,path=install
    first=Process(101,'WeChatAppEx.exe',path);second=Process(102,'WeChatAppEx.exe',path)
    approved=[w.BlockingProcess(101,'wechatappex.exe',1.5),w.BlockingProcess(102,'wechatappex.exe',1.5)]
    if problem=='other_install':
        other=root.parent/'other'/'WeChatAppEx.exe';other.parent.mkdir();other.write_bytes(b'synthetic')
        second.path=str(other)
    elif problem=='reused_pid':second.created=2.5
    else:approved=approved[:1]
    processes=[first,second];by_id={p.pid:p for p in processes}
    monkeypatch.setattr(w.psutil,'process_iter',lambda:[p for p in processes if p.alive])
    monkeypatch.setattr(w.psutil,'Process',lambda pid:by_id[pid])
    with pytest.raises(w.VersionError):w.close_residual_helpers(approved,root)
    assert first.terminated==0 and second.terminated==0


def test_main_restarting_aborts_before_helper_termination(install,monkeypatch):
    root,path=install
    helper=Process(101,'WeChatAppEx.exe',path);main=Process(102,'Weixin.exe',root/'Weixin.exe')
    calls=[0]
    def scans():
        calls[0]+=1
        return [helper] if calls[0]==1 else [helper,main]
    monkeypatch.setattr(w.psutil,'process_iter',scans)
    monkeypatch.setattr(w.psutil,'Process',lambda pid:helper)
    with pytest.raises(w.VersionError):w.close_residual_helpers([w.BlockingProcess(101,'wechatappex.exe',1.5)],root)
    assert helper.terminated==0


def test_uncertain_identity_and_unreadable_process_names_fail_closed(install,monkeypatch):
    root,path=install
    with pytest.raises(w.VersionError):w.close_residual_helpers([w.BlockingProcess(101,'wechatappex.exe',None)],root)
    process=Process(101,'WeChatAppEx.exe',path)
    def denied():raise psutil.AccessDenied(101)
    process.name=denied
    monkeypatch.setattr(w.psutil,'process_iter',lambda:[process])
    with pytest.raises(w.VersionError,match='无法确认'):w.ensure_wechat_stopped()

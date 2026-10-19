import json
import secrets
from contextlib import closing
from pathlib import Path
import pytest
from sqlcipher3 import dbapi2 as sql
from editor.offline import Context, OfflineError
from editor.local_remark import OfflineRemark, payload, STRATEGY
from editor.cipher_stage import open_cipher
from editor.observe import read_remark
from editor.core import Store, State
from editor.trace import RemarkTimeline


class FileLock:
    def __init__(self, path): self.path=path
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def read(self): return self.path.read_bytes()
    def write(self, data): self.path.write_bytes(data)


@pytest.fixture
def fixture(tmp_path):
    root=tmp_path/'account'
    source=root/'db_storage/contact/contact.db';source.parent.mkdir(parents=True)
    key=secrets.token_bytes(32)
    with closing(sql.connect(source)) as db:
        db.execute('PRAGMA key = "x\''+key.hex()+'\'"')
        db.execute('CREATE TABLE contact(username TEXT PRIMARY KEY,nick_name TEXT,remark TEXT,local_type INTEGER,flag INTEGER,opaque BLOB)')
        db.executemany('INSERT INTO contact VALUES(?,?,?,?,?,?)',[
            ('friend','CCCCC','A',1,917,b'preserve\x00'),('other','other-name','other-remark',1,13,b'opaque')])
        db.execute('CREATE TABLE sync_queue(id INTEGER,payload BLOB)')
        db.execute('INSERT INTO sync_queue VALUES(1,?)',(b'untouched-pending-operation',))
        db.commit()
    context=Context(source,root,'account',tmp_path/'Weixin.exe',key,'4.1.15.56')
    service=OfflineRemark(tmp_path/'backups',_lock=FileLock,_guard=lambda:None,
                          _validate=lambda c:None,_version=lambda c:None,_read_version=lambda c:None)
    return context,service


def files(context):
    return {suffix:Path(str(context.source)+suffix).read_bytes() if Path(str(context.source)+suffix).exists() else None
            for suffix in ('','-wal','-shm')}


def residual(context):
    with closing(open_cipher(context.source,context.key)) as db:
        db.execute('PRAGMA journal_mode=WAL');db.execute('PRAGMA wal_autocheckpoint=0')
        db.execute('UPDATE contact SET flag=918 WHERE username=?',('friend',));db.commit()
        snapshot=files(context)
    for suffix,data in snapshot.items():
        if data is not None:Path(str(context.source)+suffix).write_bytes(data)


@pytest.mark.parametrize('with_wal',[False,True])
def test_only_desktop_remark_changes_and_all_other_fields_and_tables_preserved(fixture,with_wal):
    context,service=fixture
    if with_wal:residual(context)
    before=files(context)
    report=service.probe(context,'friend',payload('B'))
    assert files(context)==before
    assert service.original_name(context,'friend',payload('B'))=='A'
    assert report['write_strategy']==STRATEGY
    assert report['target_non_remark_fields_unchanged'] is True
    assert report['remote_non_sync_verified'] is False
    with pytest.raises(OfflineError):service.apply(context,'friend',payload('B'))
    bundle=Path(service.apply(context,'friend',payload('B'),_candidate_trial=True))
    with closing(open_cipher(context.source,context.key)) as db:
        assert db.execute('SELECT nick_name,remark,flag,opaque FROM contact WHERE username=?',('friend',)).fetchone()==(
            'CCCCC','B',918 if with_wal else 917,b'preserve\x00')
        assert db.execute('SELECT nick_name,remark,flag FROM contact WHERE username=?',('other',)).fetchone()==('other-name','other-remark',13)
        assert db.execute('SELECT * FROM sync_queue').fetchall()==[(1,b'untouched-pending-operation')]
    assert (bundle/'original.enc').read_bytes()==before['']
    assert json.loads((bundle/'operation.json').read_text())['write_strategy']==STRATEGY
    with pytest.raises(OfflineError):service.apply(context,'friend',payload('B'),_candidate_trial=True)


@pytest.mark.parametrize('fault',['after_main','after_wal','after_shm'])
def test_contact_write_failure_rolls_back_complete_original_set(fixture,fault):
    context,service=fixture;residual(context);before=files(context)
    service.probe(context,'friend',payload('B'))
    def fail(stage):
        if stage==fault:raise RuntimeError('synthetic failure')
    with pytest.raises(OfflineError,match='已恢复'):
        service.apply(context,'friend',payload('B'),_candidate_trial=True,_fault=fail)
    assert files(context)==before


def test_no_deletion_and_restore_is_a_proved_update_to_saved_original(fixture):
    context,service=fixture
    with pytest.raises(OfflineError):service.invalidate(context,'friend')
    with pytest.raises(OfflineError):service.apply(context,'friend',payload('B'),_candidate_trial=True,_invalidate=True)
    service.probe(context,'friend',payload('B'));service.apply(context,'friend',payload('B'),_candidate_trial=True)
    service.probe(context,'friend',payload('A',restore=True))
    service.apply(context,'friend',payload('A',restore=True),_candidate_trial=True)
    seen=read_remark(context,'friend',_validate=lambda c:None)
    assert seen['name']=='A' and seen['nickname']=='CCCCC'
    with closing(open_cipher(context.source,context.key)) as db:
        assert db.execute('SELECT count(*) FROM contact').fetchone()[0]==2


def test_unknown_contact_trigger_is_rejected_before_original_write(fixture):
    context,service=fixture
    with closing(open_cipher(context.source,context.key)) as db:
        db.execute('CREATE TRIGGER side_effect AFTER UPDATE ON contact BEGIN DELETE FROM sync_queue; END');db.commit()
    before=files(context)
    with pytest.raises(Exception,match='触发器'):service.probe(context,'friend',payload('B'))
    assert files(context)==before


def test_value_is_bound_and_unicode_preserved(fixture):
    context,service=fixture;name="电脑专用🙂'; DROP TABLE contact; --"
    service.probe(context,'friend',payload(name));service.apply(context,'friend',payload(name),_candidate_trial=True)
    before=files(context)
    assert read_remark(context,'friend',_validate=lambda c:None)['name']==name
    assert files(context)==before


def test_store_persists_b_without_losing_a_or_claiming_remote_safety(tmp_path):
    store=Store(tmp_path)
    store.save_remark('account','friend','A','B');store.record_remark('account','friend',applied=True)
    store.save_remark('account','friend','B','B')
    row=Store(tmp_path).remark_rows()[0]
    assert row['original_name']=='A' and row['desired_name']=='B' and row['last_applied']
    store.record_remark('account','friend',observed={'name':'B'})
    assert store.remark_rows()[0]['state']==State.UNKNOWN
    store.record_remark('account','friend',observed={'name':'A'})
    assert store.remark_rows()[0]['state']==State.INVALID
    store.clear_remark('account','friend');assert store.remark_rows()==[]


def test_report_never_contains_actual_remarks_nicknames_or_identity():
    timeline=RemarkTimeline('4.1.15.56','private-B')
    timeline.add(0,{'name':'private-B','nickname':'private-CCCCC','local_type':1},False)
    timeline.add(2,{'name':'private-A','nickname':'private-CCCCC','local_type':1},True)
    report=timeline.report('test')
    assert report['replacement_after_custom_observed'] is True
    assert report['remote_non_sync_verified'] is False
    assert report['client_rendering_verified'] is False
    assert 'private-' not in json.dumps(report)


@pytest.mark.parametrize('name',['','x'*65,'x\n','x\x00'])
def test_bad_names_are_rejected(name):
    with pytest.raises(OfflineError):payload(name)

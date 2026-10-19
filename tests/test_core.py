import sqlite3
from io import BytesIO
from pathlib import Path
import pytest
from PIL import Image
from editor.core import Store, State, Writer, UnsupportedWrite, normalize_image, digest
from editor.reader import ContactReader

@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / 'state')

def test_image_saved_after_source_deleted_and_restart(tmp_path, store):
    src = tmp_path/'custom.webp'
    Image.new('RGB',(400,200),'red').save(src)
    avatar = normalize_image(src)
    store.save('account','friend','original',b'old','alias',avatar)
    src.unlink()
    row = Store(store.path.parent).rows()[0]
    assert row['desired_avatar'] == avatar
    assert row['avatar_hash'] == digest(avatar)
    assert Image.open(BytesIO(avatar)).size == (128,128)
    assert row['last_applied'] is None
    assert row['state'] == State.UNKNOWN.value

@pytest.mark.parametrize('data',[None,b'old',b'new'])
def test_unapplied_configuration_never_claims_success_or_expiry(store,data):
    store.save('a','f','name',b'old',None,b'new')
    assert store.verify_all(lambda a,f:data)[0][2] == State.UNKNOWN
    assert store.rows()[0]['last_verified']

def test_only_prior_application_can_expire_and_db_match_is_not_display_proof(store):
    store.save('a','f','name',b'old','newname',b'new')
    with store.connect() as db:
        db.execute("UPDATE profiles SET last_applied='2026-10-19T00:00:00Z'")
    assert store.verify_all(lambda a,f:b'changed')[0][2] == State.INVALID
    assert store.verify_all(lambda a,f:b'new')[0][2] == State.UNKNOWN

def test_failure_does_not_expose_private_exception(store):
    store.save('a','f','name',None,'alias',None)
    def fail(*args): raise RuntimeError('secret-key')
    result = store.verify_all(fail)
    assert result[0][2] == State.UNKNOWN
    assert 'secret-key' not in str(result)

def test_accounts_isolated_and_original_backup_preserved(store):
    store.save('a','f','first',b'original',None,b'v1')
    store.save('a','f','changed',b'other',None,b'v2')
    store.save('b','f','other-account',None,'alias',None)
    row = store.rows()[0]
    assert row['original_name'] == 'first'
    assert row['original_avatar'] == b'original'
    store.clear_config('a','f')
    assert [r['account'] for r in store.rows()] == ['b']

def test_writer_refuses_all_mutations():
    with pytest.raises(UnsupportedWrite): Writer().apply()
    with pytest.raises(UnsupportedWrite): Writer().restore()

def test_reader_uses_read_only_handle_and_parameterized_identity(tmp_path):
    path=tmp_path/'contact.db'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE contact (username TEXT,nick_name TEXT,remark TEXT,local_type INTEGER)')
        db.execute('INSERT INTO contact VALUES (?,?,?,?)',("wxid_'",'昵称','备注',1))
    reader=ContactReader.__new__(ContactReader)
    reader._db_files=[('contact.db',str(path),path.stat().st_size)]
    reader._open=lambda rel: sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)
    # Upstream _open supplies sqlite3.Row; mimic that contract.
    def open_ro(rel):
        db=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True); db.row_factory=sqlite3.Row
        return db
    reader._open=open_ro
    before=path.read_bytes()
    assert reader.contacts()[0]['username']=="wxid_'"
    with pytest.raises(sqlite3.OperationalError):
        with reader._open_named('contact.db') as db: db.execute('DELETE FROM contact')
    assert path.read_bytes()==before
    with pytest.raises(RuntimeError): reader._open_named('head_image.db')

def test_reader_excludes_chat_databases(tmp_path,monkeypatch):
    from editor.vendor.reader import WeChatDB
    files=[('contact/contact.db','/a/contact.db',1),('head/head_image.db','/a/head_image.db',1),('message/message_0.db','/a/message_0.db',1)]
    monkeypatch.setattr(WeChatDB,'_collect_db_files',lambda self:files)
    r=ContactReader.__new__(ContactReader)
    assert len(r._collect_db_files())==2
    assert r._stable_key_dirs()==[]
    assert not r._try_other_accounts()

import secrets
import sqlite3
from contextlib import closing
from pathlib import Path
import pytest
# The optional engine only runs in the Linux research environment.
sql = pytest.importorskip('sqlcipher3.dbapi2')
from editor.cipher_stage import AvatarStage, CipherError, verify_pages, open_cipher
from editor.vendor.reader import _verify_enc_key, _decrypt_page

@pytest.fixture
def encrypted(tmp_path):
    path=tmp_path/'head_image.db'; key=secrets.token_bytes(32)
    with closing(sql.connect(path)) as db:
        db.execute('PRAGMA key = "x\''+key.hex()+'\'"')
        db.execute('CREATE TABLE head_image(username TEXT PRIMARY KEY,image_buffer BLOB,md5 TEXT,update_time INTEGER)')
        db.executemany('INSERT INTO head_image VALUES (?,?,?,?)', [('friend',b'old','',0),('other',b'x'*10000,'',0)])
        db.commit()
    return path,key

def test_real_sqlcipher_roundtrip_stage_does_not_modify_source(encrypted):
    path,key=encrypted; original=path.read_bytes()
    assert _verify_enc_key(key,original[:4096])
    with AvatarStage(path,key,lambda:None) as stage:
        stage.replace_avatar('friend',b'custom')
        with closing(open_cipher(stage.path,key)) as db:
            assert db.execute('SELECT image_buffer FROM head_image WHERE username=?',('friend',)).fetchone()[0] == b'custom'
            assert db.execute('SELECT length(image_buffer) FROM head_image WHERE username=?',('other',)).fetchone()[0] == 10000
        assert path.read_bytes() == original
        verify_pages(stage.path,key)
        assert _verify_enc_key(key,stage.path.read_bytes()[:4096])
        # Existing upstream reader can consume the staged encrypted output.
        raw=stage.path.read_bytes(); plain=Path(stage.temp.name)/'decrypted.db'
        plain.write_bytes(b''.join(_decrypt_page(key,raw[i:i+4096],i//4096+1) for i in range(0,len(raw),4096)))
        with closing(sqlite3.connect(plain)) as db:
            assert db.execute('SELECT image_buffer FROM head_image WHERE username=?',('friend',)).fetchone()[0] == b'custom'
        staged=stage.path
    assert not staged.exists()
    assert path.read_bytes() == original

def test_transaction_failure_rolls_back(encrypted):
    path,key=encrypted
    def fail(): raise RuntimeError('injected fault')
    with AvatarStage(path,key,lambda:None) as stage:
        with pytest.raises(RuntimeError): stage.replace_avatar('friend',b'new',before_commit=fail)
        with closing(open_cipher(stage.path,key)) as db:
            assert db.execute('SELECT image_buffer FROM head_image WHERE username=?',('friend',)).fetchone()[0] == b'old'
        verify_pages(stage.path,key)

def test_reject_wal_instead_of_dropping_it(encrypted):
    path,key=encrypted; Path(str(path)+'-wal').write_bytes(b'wal')
    with pytest.raises(CipherError):
        with AvatarStage(path,key,lambda:None): pass

def test_page_corruption_rejected(encrypted):
    path,key=encrypted; data=bytearray(path.read_bytes()); data[-4096+100] ^= 1; path.write_bytes(data)
    with pytest.raises(CipherError): verify_pages(path,key)

def test_wrong_key_rejected(encrypted):
    path,key=encrypted
    with pytest.raises(CipherError): verify_pages(path,secrets.token_bytes(32))

def test_missing_friend_not_inserted(encrypted):
    path,key=encrypted
    with AvatarStage(path,key,lambda:None) as stage:
        with pytest.raises(CipherError): stage.replace_avatar('missing',b'new')

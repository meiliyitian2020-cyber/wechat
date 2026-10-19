"""Experimental SQLCipher staging only. Never promotes files into WeChat.

Only the standard 4096-byte SQLCipher4 raw-key format is supported here.
WCDB compatibility and actual client rendering remain unverified.
The pinned SQLCipher engine provides Linux and Windows x64 wheels.
Windows execution remains unverified. Writer does not call this module.
"""
from contextlib import closing
from pathlib import Path
import hashlib
import hmac
import os
import shutil
import struct
import tempfile
import time
from .backup import BackupError, sha256

PAGE = 4096
RESERVE = 80

class CipherError(RuntimeError):
    pass

def verify_pages(path, key):
    if not isinstance(key, bytes) or len(key) != 32:
        raise CipherError('仅支持经适配的 32 字节 raw key 格式')
    size = path.stat().st_size
    if size == 0 or size % PAGE:
        raise CipherError('数据库页长度不符合已适配格式')
    with path.open('rb') as stream:
        salt = stream.read(16)
        mac_key = hashlib.pbkdf2_hmac('sha512', key, bytes(v ^ 0x3a for v in salt), 2, 32)
        stream.seek(0)
        for page_number in range(1, size // PAGE + 1):
            page = stream.read(PAGE)
            start = 16 if page_number == 1 else 0
            body = page[start:PAGE - RESERVE + 16] + struct.pack('<I',page_number)
            if len(page) != PAGE or not hmac.compare_digest(hmac.new(mac_key,body,hashlib.sha512).digest(),page[-64:]):
                raise CipherError('数据库页面完整性校验失败')

def open_cipher(path, key):
    from sqlcipher3 import dbapi2 as sql
    db = sql.connect(path)
    try:
        db.execute('PRAGMA key = "x\'' + key.hex() + '\'"')
        db.execute('PRAGMA cipher_compatibility = 4')
        db.execute('PRAGMA cipher_memory_security = ON')
        if db.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
            raise CipherError('SQLCipher 完整性检查失败')
        return db
    except BaseException:
        db.close()
        raise

class AvatarStage:
    """Transaction experiment on an encrypted temporary copy, never the source."""
    def __init__(self, source, key, ensure_stopped):
        self.temp = None
        self.source = Path(source)
        if self.source.name != 'head_image.db' or self.source.is_symlink():
            raise CipherError('仅允许离线头像库实验')
        if not isinstance(key,bytes) or len(key)!=32:
            raise CipherError('不支持的密钥格式')
        self.key = key
        self.guard = ensure_stopped

    def __enter__(self):
        self.guard()
        if any(Path(str(self.source)+suffix).exists() for suffix in ('-wal','-shm')):
            raise CipherError('存在 WAL/SHM；当前实验路径不支持，禁止忽略或删除')
        verify_pages(self.source,self.key)
        self.original_hash = sha256(self.source)
        self.temp = tempfile.TemporaryDirectory(prefix='avatar-stage-')
        self.path = Path(self.temp.name)/'head_image.db'
        try:
            shutil.copyfile(self.source,self.path)
            self.guard()
            if sha256(self.source)!=self.original_hash or sha256(self.path)!=self.original_hash:
                raise CipherError('复制期间源数据库发生变化')
            if any(Path(str(self.source)+suffix).exists() for suffix in ('-wal','-shm')):
                raise CipherError('复制期间出现 WAL/SHM')
            verify_pages(self.path,self.key)
            return self
        except BaseException:
            self.temp.cleanup()
            self.key = None
            raise

    def replace_avatar(self, friend, avatar, before_commit=None):
        verify_pages(self.path,self.key)
        with closing(open_cipher(self.path,self.key)) as db:
            columns = {r[1] for r in db.execute('PRAGMA table_info(head_image)')}
            if not {'username','image_buffer','md5','update_time'} <= columns:
                raise CipherError('未知头像表结构')
            with db:
                db.execute('BEGIN IMMEDIATE')
                count = db.execute('SELECT count(*) FROM head_image WHERE username=?',(friend,)).fetchone()[0]
                if count != 1:
                    raise CipherError('目标联系人不存在或记录不唯一')
                db.execute('UPDATE head_image SET image_buffer=?,md5=?,update_time=? WHERE username=?',
                    (avatar,hashlib.md5(avatar).hexdigest(),int(time.time()),friend))
                if db.execute('SELECT image_buffer FROM head_image WHERE username=?',(friend,)).fetchone()[0] != avatar:
                    raise CipherError('暂存记录验证失败')
                if before_commit:
                    before_commit()  # Fault injection; never used by the application.
        verify_pages(self.path,self.key)
        with closing(open_cipher(self.path,self.key)) as db:
            if db.execute('SELECT image_buffer FROM head_image WHERE username=?',(friend,)).fetchone()[0] != avatar:
                raise CipherError('提交后暂存记录验证失败')
        if sha256(self.source)!=self.original_hash:
            raise CipherError('实验期间源文件发生变化')

    def __exit__(self,*args):
        self.key = None
        if self.temp:
            self.temp.cleanup()

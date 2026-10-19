"""Restricted adapter for the pinned Apache-2.0 read-only implementation."""
from contextlib import closing
from pathlib import Path
import tempfile
from .vendor.reader import WeChatDB, auto_detect_db_dir, list_accounts

class ContactReader(WeChatDB):
    def _load_or_extract_keys(self, master_key=None):
        import os
        if os.name != 'nt':
            raise RuntimeError('真实微信读取仅支持 Windows')
        self.master_key = None
        self.cfg_dword = None
        self._keys = self.extract_keys()
        self.unkeyed = [rel for rel, _, _ in self._db_files if not self._key_works(rel)]
        if self.unkeyed:
            raise RuntimeError('无法验证所选账号联系人数据库密钥')

    def _collect_db_files(self):
        return [x for x in super()._collect_db_files()
                if Path(x[1]).name in {'contact.db', 'head_image.db'}]

    def _save_keys(self):
        pass  # Do not persist database keys to plaintext JSON.

    def _stable_key_dirs(self):
        return []

    def _load_key_cache(self, path):
        return {}  # Do not consume another application's saved secrets.

    def _auto_diagnose_key_failure(self, rel):
        pass  # Never launch the upstream diagnostic subprocess.

    def _try_other_accounts(self):
        return False

    def _select_account_by_keys(self, candidates):
        return False

    def _open_named(self, name):
        matches = [r for r, path, _ in self._db_files if Path(path).name == name]
        if len(matches) != 1:
            raise RuntimeError('缺少或存在多个目标数据库，无法安全确定读取来源')
        return self._open(matches[0])

    def contacts(self):
        with closing(self._open_named('contact.db')) as db:
            return [dict(r) for r in db.execute('''SELECT username,nick_name,remark
                FROM contact WHERE local_type=1 ORDER BY remark,nick_name''')]

    def avatar(self, friend):
        with closing(self._open_named('head_image.db')) as db:
            row = db.execute('SELECT image_buffer FROM head_image WHERE username=?', (friend,)).fetchone()
            return bytes(row[0]) if row and row[0] else None

class Session:
    def __init__(self, root, account):
        self.temp = tempfile.TemporaryDirectory(prefix='local-profile-')
        try:
            self.reader = ContactReader(db_dir=str(root), account=account, workdir=self.temp.name)
            if self.reader.account != account:
                raise RuntimeError('读取器切换了账号，已拒绝结果')
        except BaseException:
            self.temp.cleanup()
            raise

    def close(self):
        self.reader._keys.clear()
        self.temp.cleanup()

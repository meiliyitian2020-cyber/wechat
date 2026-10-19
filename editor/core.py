"""Local profile configuration and scoped status. Experimental writer is in offline.py."""
from __future__ import annotations
from contextlib import closing
from datetime import datetime, timezone
from enum import Enum
from io import BytesIO
from pathlib import Path
import hashlib
import sqlite3
from PIL import Image, ImageOps

class State(str, Enum):
    UNMODIFIED = '未修改'
    VALID = '修改有效'
    INVALID = '修改失效'
    UNKNOWN = '无法验证'

class UnsupportedWrite(RuntimeError):
    pass

def now():
    return datetime.now(timezone.utc).isoformat()

def normalize_image(path: Path) -> bytes:
    with Image.open(path) as im:
        if im.format not in {'JPEG', 'PNG', 'WEBP'}:
            raise ValueError('仅支持 JPG、PNG、WEBP')
        im.load()
        im = ImageOps.exif_transpose(im)
        im = ImageOps.fit(im.convert('RGBA'), (128, 128), method=Image.Resampling.LANCZOS)
        out = BytesIO()
        im.save(out, 'PNG')
        return out.getvalue()

def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

class Store:
    """Persist image bytes transactionally, never just a path to user images."""
    def __init__(self, root: Path):
        root.mkdir(parents=True, exist_ok=True)
        self.path = root / 'profiles.sqlite3'
        with closing(self.connect()) as db, db:
            db.execute('''CREATE TABLE IF NOT EXISTS profiles (
              account TEXT, friend TEXT, original_name TEXT NOT NULL,
              original_avatar BLOB, desired_name TEXT, desired_avatar BLOB,
              avatar_hash TEXT, last_applied TEXT, last_verified TEXT,
              state TEXT NOT NULL, scope TEXT NOT NULL,
              PRIMARY KEY(account, friend))''')

    def remark_rows(self):
        with closing(self.connect()) as db, db:
            db.execute("""CREATE TABLE IF NOT EXISTS local_remark_profiles (
                account TEXT, friend TEXT, original_name TEXT NOT NULL, desired_name TEXT NOT NULL,
                last_applied TEXT, last_verified TEXT, state TEXT NOT NULL, scope TEXT NOT NULL,
                PRIMARY KEY(account,friend))""")
            return [dict(r) for r in db.execute('SELECT * FROM local_remark_profiles ORDER BY account,friend')]

    def save_remark(self, account, friend, original, desired):
        from .local_remark import payload
        payload(desired)
        if not account or not friend or not isinstance(original, str): raise ValueError('备注配置缺少目标')
        self.remark_rows()
        with closing(self.connect()) as db, db:
            db.execute("""INSERT INTO local_remark_profiles VALUES (?,?,?,?,NULL,NULL,?,?)
                ON CONFLICT(account,friend) DO UPDATE SET desired_name=excluded.desired_name,
                last_applied=NULL,last_verified=NULL,state=excluded.state,scope=excluded.scope
                WHERE local_remark_profiles.desired_name IS NOT excluded.desired_name""",
                (account,friend,original,desired,State.UNKNOWN.value,'仅保存本机备注试验配置，尚未写入微信'))

    def record_remark(self, account, friend, *, applied=False, restored=False, observed=None):
        self.remark_rows()
        with closing(self.connect()) as db, db:
            row=db.execute('SELECT * FROM local_remark_profiles WHERE account=? AND friend=?',(account,friend)).fetchone()
            if row is None: return
            state=State.UNKNOWN
            scope='无法读取当前备注缓存；未验证客户端显示与远端不同步'
            last=row['last_applied']
            if applied:
                last=now();scope='仅联系人备注缓存写入校验通过；客户端显示和远端不同步尚未验证'
            elif restored:
                last=None;scope='已写回保存的原备注缓存；客户端显示及远端状态尚未验证'
            elif observed is not None:
                if observed.get('name')==row['desired_name']:
                    scope='备注缓存一致；客户端显示与远端不同步仍未验证'
                elif last:
                    state=State.INVALID;scope='备注缓存已变化；未验证客户端显示'
            db.execute('UPDATE local_remark_profiles SET last_applied=?,last_verified=?,state=?,scope=? WHERE account=? AND friend=?',
                       (last,now(),state.value,scope,account,friend))

    def clear_remark(self, account, friend):
        self.remark_rows()
        with closing(self.connect()) as db, db:
            db.execute('DELETE FROM local_remark_profiles WHERE account=? AND friend=?',(account,friend))

    def connect(self):
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        return db

    def save(self, account, friend, original_name, original_avatar, name, avatar):
        if not account or not friend or (name is None and avatar is None):
            raise ValueError('请选择账号、好友及至少一项替换配置')
        with closing(self.connect()) as db, db:
            db.execute('''INSERT INTO profiles VALUES (?,?,?,?,?,?,?,NULL,NULL,?,?)
              ON CONFLICT(account,friend) DO UPDATE SET
              desired_name=excluded.desired_name, desired_avatar=excluded.desired_avatar,
              avatar_hash=excluded.avatar_hash, state=excluded.state,
              scope=excluded.scope, last_verified=NULL, last_applied=NULL
              WHERE NOT (profiles.desired_avatar IS excluded.desired_avatar
                         AND profiles.desired_name IS excluded.desired_name)''',
              (account, friend, original_name, original_avatar, name, avatar,
               digest(avatar) if avatar else None, State.UNKNOWN.value,
               '仅保存配置，尚未应用至微信'))

    def rows(self):
        with closing(self.connect()) as db:
            return [dict(r) for r in db.execute('SELECT * FROM profiles ORDER BY account,friend')]

    def record_applied(self, account, friend, backup):
        with closing(self.connect()) as db, db:
            db.execute('CREATE TABLE IF NOT EXISTS operations (account TEXT, friend TEXT, backup TEXT, time TEXT, action TEXT)')
            db.execute('INSERT INTO operations VALUES (?,?,?,?,?)',(account,friend,backup,now(),'apply'))
            db.execute('UPDATE profiles SET last_applied=?,last_verified=?,state=?,scope=? WHERE account=? AND friend=?',
                       (now(),now(),State.UNKNOWN.value,'加密头像库写入并校验通过；客户端显示尚未验证',account,friend))

    def record_invalidated(self, account, friend, backup):
        with closing(self.connect()) as db, db:
            db.execute('CREATE TABLE IF NOT EXISTS operations (account TEXT, friend TEXT, backup TEXT, time TEXT, action TEXT)')
            db.execute('INSERT INTO operations VALUES (?,?,?,?,?)',(account,friend,backup,now(),'invalidate'))
            db.execute('UPDATE profiles SET last_applied=NULL,last_verified=?,state=?,scope=? WHERE account=? AND friend=?',
                       (now(),State.UNKNOWN.value,'本地头像记录已清除，等待微信重新获取当前头像；替换配置保留',account,friend))

    def clear_config(self, account, friend):
        # This deliberately does not claim to restore any WeChat resource.
        with closing(self.connect()) as db, db:
            db.execute('DELETE FROM profiles WHERE account=? AND friend=?', (account, friend))

    def verify_all(self, observe):
        results = []
        for row in self.rows():
            state, scope = State.UNKNOWN, '未获得当前微信资源；不能验证客户端显示'
            try:
                current = observe(row['account'], row['friend'])
                if row['desired_avatar'] is not None and current is not None:
                    if digest(current) != row['avatar_hash']:
                        scope = '观测头像与配置不同；配置尚未成功应用，不能判为失效'
                        if row['last_applied']:
                            state = State.INVALID
                            scope = '当前读取的数据库头像已变化；未验证客户端显示'
                    else:
                        scope = '仅读取的数据库头像字节一致；未验证客户端显示或昵称'
            except Exception:
                pass  # No database keys or contact data in logs.
            with closing(self.connect()) as db, db:
                db.execute('UPDATE profiles SET last_verified=?,state=?,scope=? WHERE account=? AND friend=?',
                           (now(), state.value, scope, row['account'], row['friend']))
            results.append((row['account'], row['friend'], state, scope))
        return results

class Writer:
    def apply(self, *args, **kwargs):
        raise UnsupportedWrite('4.1.13.65 的加密写回和客户端缓存读取路径尚未验证，禁止写入。')

    def restore(self, *args, **kwargs):
        raise UnsupportedWrite('尚无已验证的恢复后端；未改动微信数据。清除配置不等于恢复。')

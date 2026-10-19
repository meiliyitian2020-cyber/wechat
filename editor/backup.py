"""Verified offline backup bundles. This module never writes to source files.

The process check is a prerequisite, not an exclusive file lock. Production
writeback must additionally hold appropriate Windows locks throughout mutation.
"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import uuid
from datetime import datetime, timezone

class BackupError(RuntimeError):
    pass

def sha256(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()

def checked_path(root, relative):
    relative = Path(relative)
    if relative.is_absolute() or not relative.parts or '..' in relative.parts:
        raise BackupError('备份资源路径无效')
    path = root / relative
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise BackupError('备份资源不能包含符号链接')
    if not path.resolve().is_relative_to(root):
        raise BackupError('资源路径超出账号目录')
    return path

def inventory(root, resources):
    result = {}
    for relative in resources:
        path = checked_path(root, relative)
        key = Path(relative).as_posix()
        if key in result:
            raise BackupError('重复资源')
        if not path.exists():
            result[key] = None
            continue
        if not path.is_file():
            raise BackupError('备份资源必须为普通文件')
        result[key] = {'size': path.stat().st_size, 'sha256': sha256(path)}
    return result

class BackupManager:
    def __init__(self, destination):
        self.destination = Path(destination).resolve()

    def create(self, account_root, databases, ensure_stopped):
        """Save each requested DB and presence/absence of its WAL and SHM.

        ensure_stopped must raise if WeChat is running or status is uncertain.
        All paths are account-relative. Message databases are never accepted.
        """
        root = Path(account_root).resolve(strict=True)
        if self.destination.is_relative_to(root):
            raise BackupError('备份目录必须位于微信账号目录之外')
        resources = []
        for relative in databases:
            relative = Path(relative)
            if relative.name not in {'contact.db', 'head_image.db'}:
                raise BackupError('仅允许联系人和头像库；禁止备份聊天数据库')
            checked_path(root, relative)
            if not (root / relative).is_file():
                raise BackupError('目标数据库不存在')
            resources.extend([relative.as_posix(), relative.as_posix()+'-wal', relative.as_posix()+'-shm'])
        if not resources:
            raise BackupError('没有需要备份的资源')
        ensure_stopped()
        before = inventory(root, resources)
        self.destination.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix='.incomplete-', dir=self.destination))
        try:
            for relative, expected in before.items():
                if expected is None:
                    continue
                source = checked_path(root, relative)
                target = staging / 'files' / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                with source.open('rb') as src, target.open('xb') as dst:
                    shutil.copyfileobj(src, dst)
                    dst.flush()
                    os.fsync(dst.fileno())
                if target.stat().st_size != expected['size'] or sha256(target) != expected['sha256']:
                    raise BackupError('备份副本校验失败')
            ensure_stopped()
            if inventory(root, resources) != before:
                raise BackupError('备份期间微信数据发生变化，已放弃备份')
            manifest = {'schema': 1, 'created_at': datetime.now(timezone.utc).isoformat(),
                        'files': before, 'scope': 'offline-stable-copy; no exclusive source lock'}
            with (staging / 'manifest.json').open('x', encoding='utf-8') as out:
                json.dump(manifest, out, ensure_ascii=False, indent=2)
                out.flush()
                os.fsync(out.fileno())
            final = self.destination / uuid.uuid4().hex
            staging.rename(final)
            return final
        except BaseException:
            shutil.rmtree(staging)
            raise

    @staticmethod
    def verify(bundle):
        root = Path(bundle).resolve(strict=True)
        manifest_path = checked_path(root, 'manifest.json')
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        if manifest.get('schema') != 1 or not isinstance(manifest.get('files'), dict) or not manifest['files']:
            raise BackupError('备份清单格式无效')
        files_root = root / 'files'
        if files_root.is_symlink():
            raise BackupError('备份资源目录无效')
        for relative, expected in manifest['files'].items():
            path = checked_path(files_root, relative)
            if expected is None:
                if path.exists():
                    raise BackupError('备份记录为不存在的文件实际存在')
            elif (not path.is_file() or path.stat().st_size != expected['size']
                  or sha256(path) != expected['sha256']):
                raise BackupError('备份文件丢失或已损坏')
        return manifest

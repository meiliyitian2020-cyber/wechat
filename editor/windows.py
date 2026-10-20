import os
import time
from dataclasses import dataclass
from pathlib import Path
import psutil

TARGET = '4.1.13.65'
# New updates are read/probe candidates only. A candidate is NOT write-enabled.
READ_VERSIONS = frozenset({'4.1.13.65', '4.1.15.56', '4.1.15.65'})
WRITE_VERSIONS = frozenset({'4.1.13.65'})

class VersionError(RuntimeError):
    """User-safe version discovery/policy error; contains no account data."""
    pass

def check_read_version(detected, loaded=None):
    if detected not in READ_VERSIONS:
        raise VersionError(f'版本 {detected} 尚未适配，禁止读取和修改')
    if loaded is not None and detected != loaded:
        raise VersionError('微信在读取后发生版本变化，请重新读取账号；禁止沿用旧版本密钥上下文')

def check_write_version(detected, loaded):
    check_read_version(detected, loaded)
    if detected not in WRITE_VERSIONS:
        raise VersionError(f'{detected} 仅开放读取和副本兼容性检查，原始数据库写入尚未开放')

def version(path):
    import ctypes
    from ctypes import wintypes as w
    class Info(ctypes.Structure):
        _fields_ = [(n, w.DWORD) for n in ('signature','structure','fileMS','fileLS',
                    'productMS','productLS','mask','flags','os','type','subtype','dateMS','dateLS')]
    api = ctypes.WinDLL('version', use_last_error=True)
    api.GetFileVersionInfoSizeW.argtypes = [w.LPCWSTR, ctypes.POINTER(w.DWORD)]
    api.GetFileVersionInfoSizeW.restype = w.DWORD
    api.GetFileVersionInfoW.argtypes = [w.LPCWSTR, w.DWORD, w.DWORD, w.LPVOID]
    api.GetFileVersionInfoW.restype = w.BOOL
    api.VerQueryValueW.argtypes = [w.LPCVOID, w.LPCWSTR, ctypes.POINTER(w.LPVOID), ctypes.POINTER(w.UINT)]
    api.VerQueryValueW.restype = w.BOOL
    size = api.GetFileVersionInfoSizeW(str(path), None)
    if not size:
        raise VersionError('无法读取微信版本')
    data = ctypes.create_string_buffer(size)
    if not api.GetFileVersionInfoW(str(path), 0, size, data):
        raise VersionError('无法读取微信版本')
    ptr, length = w.LPVOID(), w.UINT()
    if not api.VerQueryValueW(data, '\\', ctypes.byref(ptr), ctypes.byref(length)) or length.value < ctypes.sizeof(Info):
        raise VersionError('微信版本信息无效')
    info = ctypes.cast(ptr, ctypes.POINTER(Info)).contents
    if info.signature != 0xFEEF04BD:
        raise VersionError('微信版本签名无效')
    return '.'.join(map(str, (info.fileMS >> 16, info.fileMS & 65535, info.fileLS >> 16, info.fileLS & 65535)))

def discover():
    if os.name != 'nt':
        raise VersionError('需要 Windows 测试机；当前系统不能验证微信读取')
    paths = set()
    for p in psutil.process_iter(['name', 'exe']):
        try:
            if (p.info['name'] or '').lower() in {'weixin.exe', 'wechat.exe'} and p.info['exe']:
                paths.add(p.info['exe'])
        except (psutil.AccessDenied, psutil.NoSuchProcess):
            continue
    if not paths:
        raise VersionError('未找到运行中的微信，请先登录 Windows 微信')
    results = [(Path(p), version(p)) for p in sorted(paths)]
    if len(results) != 1:
        raise VersionError('发现多个微信安装实例，暂不支持自动选择')
    path, ver = results[0]
    check_read_version(ver)
    return path, ver

MAIN_PROCESSES = frozenset({'weixin.exe','wechat.exe'})
HELPER_PROCESSES = frozenset({'wechatappex.exe','weixinappex.exe'})

@dataclass(frozen=True)
class BlockingProcess:
    pid: int
    name: str
    created: float | None

class WechatRunningError(VersionError):
    def __init__(self, blockers):
        super().__init__('请先完全退出微信后再继续（含残留组件）')
        self.blocking_processes=tuple(blockers)


def _require_windows():
    if os.name!='nt': raise VersionError('无法在非 Windows 系统确认微信已退出')


def blocking_processes():
    _require_windows()
    blockers=[]
    try:
        for process in psutil.process_iter():
            try:
                name=process.name().lower()
                if name not in MAIN_PROCESSES|HELPER_PROCESSES: continue
                try: created=process.create_time()
                except psutil.AccessDenied: created=None
                blockers.append(BlockingProcess(process.pid,name,created))
            except psutil.NoSuchProcess:
                continue
    except psutil.AccessDenied:
        raise VersionError('无法确认微信进程状态，已禁止操作') from None
    return blockers


def ensure_wechat_stopped():
    blockers=blocking_processes()
    if blockers: raise WechatRunningError(blockers)


def close_residual_helpers(approved, installation):
    """Only called after explicit UI approval; never terminate a main process."""
    _require_windows()
    approved=tuple(approved)
    if len(approved)>16: raise VersionError('残留组件过多，不自动结束，请先正常退出相关应用')
    if not approved or any(p.name not in HELPER_PROCESSES or p.created is None for p in approved):
        raise VersionError('不能自动结束主程序或身份无法确认的进程')
    root=Path(installation).resolve(strict=True)
    current=blocking_processes()
    if any(p.name in MAIN_PROCESSES for p in current): raise WechatRunningError(current)
    allowed={(p.pid,p.name,p.created) for p in approved}
    if any((p.pid,p.name,p.created) not in allowed for p in current):
        raise VersionError('检测到新的微信进程，请重新确认名单')
    # Validate every survivor before terminating any of them.
    targets=[]
    for item in current:
        try:
            process=psutil.Process(item.pid)
            executable=Path(process.exe()).resolve(strict=True)
            if (process.name().lower()!=item.name or process.create_time()!=item.created or
                executable.name.lower()!=item.name or not executable.is_relative_to(root)):
                raise VersionError('残留组件身份或安装目录不匹配，未自动结束')
            targets.append((process,item))
        except psutil.NoSuchProcess:
            continue
        except psutil.AccessDenied:
            raise VersionError('无权核实残留组件，未自动结束') from None
    deadline=time.monotonic()+5
    for process,item in targets:
        observed=blocking_processes()
        if any(p.name in MAIN_PROCESSES for p in observed):
            raise WechatRunningError(observed)
        try:
            if process.name().lower()!=item.name or process.create_time()!=item.created:
                raise VersionError('进程身份已变化，停止处理残留组件')
            process.terminate()
            process.wait(timeout=max(.01,deadline-time.monotonic()))
        except psutil.NoSuchProcess:
            continue
        except psutil.AccessDenied:
            raise VersionError('无权结束残留组件，请使用相同权限运行工具') from None
        except psutil.TimeoutExpired:
            raise VersionError('残留组件未在限时内退出，已停止操作') from None
    ensure_wechat_stopped()

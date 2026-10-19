import os
from pathlib import Path
import psutil

TARGET = '4.1.13.65'

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
        raise RuntimeError('无法读取微信版本')
    data = ctypes.create_string_buffer(size)
    if not api.GetFileVersionInfoW(str(path), 0, size, data):
        raise RuntimeError('无法读取微信版本')
    ptr, length = w.LPVOID(), w.UINT()
    if not api.VerQueryValueW(data, '\\', ctypes.byref(ptr), ctypes.byref(length)) or length.value < ctypes.sizeof(Info):
        raise RuntimeError('微信版本信息无效')
    info = ctypes.cast(ptr, ctypes.POINTER(Info)).contents
    if info.signature != 0xFEEF04BD:
        raise RuntimeError('微信版本签名无效')
    return '.'.join(map(str, (info.fileMS >> 16, info.fileMS & 65535, info.fileLS >> 16, info.fileLS & 65535)))

def discover():
    if os.name != 'nt':
        raise RuntimeError('需要 Windows 测试机；当前系统不能验证微信读取')
    paths = set()
    for p in psutil.process_iter(['name', 'exe']):
        try:
            if (p.info['name'] or '').lower() in {'weixin.exe', 'wechat.exe'} and p.info['exe']:
                paths.add(p.info['exe'])
        except (psutil.AccessDenied, psutil.NoSuchProcess):
            continue
    if not paths:
        raise RuntimeError('未找到运行中的微信，请先登录 Windows 微信')
    results = [(Path(p), version(p)) for p in sorted(paths)]
    if len(results) != 1:
        raise RuntimeError('发现多个微信安装实例，暂不支持自动选择')
    path, ver = results[0]
    if ver != TARGET:
        raise RuntimeError(f'版本 {ver} 尚未适配；仅允许 {TARGET} 的实验性只读验证')
    return path, ver

def ensure_wechat_stopped():
    """Fail closed; do not terminate the user's WeChat process."""
    if os.name != 'nt':
        raise RuntimeError('无法在非 Windows 系统确认微信已退出')
    try:
        for process in psutil.process_iter():
            try:
                name = process.name()
            except psutil.NoSuchProcess:
                continue
            if name.lower() in {'weixin.exe', 'wechat.exe', 'wechatappex.exe', 'weixinappex.exe'}:
                raise RuntimeError('请先完全退出微信后再继续')
    except psutil.AccessDenied:
        raise RuntimeError('无法确认微信进程状态，已禁止操作') from None

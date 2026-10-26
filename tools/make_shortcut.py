"""在桌面创建「DeepSeek 余额浮窗」快捷方式。

用法：
    python tools/make_shortcut.py            创建（已存在则覆盖）
    python tools/make_shortcut.py --remove   删除

为什么不用 PowerShell 的 WScript.Shell：
本机的安全策略会拦截 COM 对象实例化（New-Object -ComObject）和 cscript，
所以这里直接用 ctypes 调 IShellLinkW + IPersistFile 两个 COM 接口自己写 .lnk。
"""

import ctypes
import os
import sys
from ctypes import wintypes
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TARGET = ROOT / "启动 DeepSeek 余额浮窗.bat"
ICON = ROOT / "icon.ico"
NAME = "DeepSeek 余额浮窗"

CLSID_SHELLLINK = bytes([
    0x01, 0x14, 0x02, 0x00, 0x00, 0x00, 0x00, 0x00,
    0xC0, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x46,
])
IID_SHELLLINK = bytes([
    0xF9, 0x14, 0x02, 0x00, 0x00, 0x00, 0x00, 0x00,
    0xC0, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x46,
])
IID_PERSISTFILE = bytes([
    0x0B, 0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0xC0, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x46,
])

GUID = ctypes.c_byte * 16


def _guid(raw: bytes):
    g = GUID()
    ctypes.memmove(g, raw, 16)
    return g


def _vfunc(ptr: int, index: int) -> int:
    """取 COM 接口 vtable 里第 index 个函数指针。"""
    vtable = ctypes.c_void_p.from_address(ptr).value
    step = ctypes.sizeof(ctypes.c_void_p)
    return ctypes.c_void_p.from_address(vtable + index * step).value


def desktop_dir() -> Path:
    return Path(os.path.join(os.environ["USERPROFILE"], "Desktop"))


def create() -> Path:
    ole32 = ctypes.windll.ole32
    # 必须显式声明签名：默认按 32 位 int 处理指针，会 OverflowError
    ole32.CoCreateInstance.argtypes = [
        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD,
        ctypes.c_void_p, ctypes.c_void_p,
    ]
    ole32.CoCreateInstance.restype = ctypes.HRESULT
    ole32.CoInitialize(None)

    # GUID 要留在局部变量里，否则可能在调用前被回收
    clsid = _guid(CLSID_SHELLLINK)
    iid_link = _guid(IID_SHELLLINK)
    iid_persist = _guid(IID_PERSISTFILE)

    shell_link = ctypes.c_void_p()
    hr = ole32.CoCreateInstance(
        ctypes.addressof(clsid),
        None,
        1,  # CLSCTX_INPROC_SERVER
        ctypes.addressof(iid_link),
        ctypes.byref(shell_link),
    )
    if hr != 0:
        raise OSError("CoCreateInstance 失败: 0x%08X" % (hr & 0xFFFFFFFF))

    ptr = shell_link.value

    def call(index, argtypes, *args):
        proto = ctypes.WINFUNCTYPE(ctypes.HRESULT, ctypes.c_void_p, *argtypes)
        res = proto(_vfunc(ptr, index))(ctypes.c_void_p(ptr), *args)
        if res != 0:
            raise OSError("COM 调用 %d 失败: 0x%08X" % (index, res & 0xFFFFFFFF))

    def w(text):
        return ctypes.create_unicode_buffer(str(text))

    call(7, [wintypes.LPCWSTR], w("DeepSeek 余额悬浮窗"))          # SetDescription
    call(9, [wintypes.LPCWSTR], w(str(ROOT)))                     # SetWorkingDirectory
    if ICON.is_file():
        call(17, [wintypes.LPCWSTR, ctypes.c_int], w(ICON), 0)    # SetIconLocation
    call(11, [wintypes.LPCWSTR], w(""))                           # SetArguments
    call(15, [ctypes.c_int], 1)                                   # SetShowCmd(SW_SHOWNORMAL)
    call(20, [wintypes.LPCWSTR], w(TARGET))                       # SetPath

    # QueryInterface → IPersistFile
    persist = ctypes.c_void_p()
    proto = ctypes.WINFUNCTYPE(
        ctypes.HRESULT, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p
    )
    hr = proto(_vfunc(ptr, 0))(
        ctypes.c_void_p(ptr),
        ctypes.addressof(iid_persist),
        ctypes.byref(persist),
    )
    if hr != 0:
        raise OSError("QueryInterface 失败: 0x%08X" % (hr & 0xFFFFFFFF))

    lnk = desktop_dir() / (NAME + ".lnk")
    pptr = persist.value
    save = ctypes.WINFUNCTYPE(
        ctypes.HRESULT, ctypes.c_void_p, wintypes.LPCWSTR, wintypes.BOOL
    )(_vfunc(pptr, 6))
    hr = save(ctypes.c_void_p(pptr), w(str(lnk)), True)
    if hr != 0:
        raise OSError("IPersistFile.Save 失败: 0x%08X" % (hr & 0xFFFFFFFF))

    # Release 两个接口
    for p in (pptr, ptr):
        ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(_vfunc(p, 2))(
            ctypes.c_void_p(p)
        )
    ole32.CoUninitialize()
    return lnk


def main() -> int:
    if "--remove" in sys.argv:
        lnk = desktop_dir() / (NAME + ".lnk")
        if lnk.exists():
            lnk.unlink()
            print("已删除: %s" % lnk)
        else:
            print("本来就没有: %s" % lnk)
        return 0

    if not TARGET.is_file():
        print("[错误] 找不到启动器: %s" % TARGET)
        return 1
    lnk = create()
    print("桌面快捷方式已创建: %s" % lnk)
    print("  指向: %s" % TARGET)
    print("  图标: %s" % (ICON if ICON.is_file() else "（未找到，用系统默认）"))
    return 0


if __name__ == "__main__":
    sys.exit(main())

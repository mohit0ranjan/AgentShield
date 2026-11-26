"""Encrypt local secrets for the current Windows account."""

import ctypes
import os
from ctypes import wintypes


class DataBlob(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]


def protect(data):
    return _transform(data, "CryptProtectData")


def unprotect(data):
    return _transform(data, "CryptUnprotectData")


def _transform(data, function):
    if os.name != "nt":
        raise OSError("Windows Data Protection API is unavailable.")
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    transform = getattr(crypt32, function)
    blob_pointer = ctypes.POINTER(DataBlob)
    transform.argtypes = [blob_pointer, ctypes.c_void_p, blob_pointer, ctypes.c_void_p,
                          ctypes.c_void_p, wintypes.DWORD, blob_pointer]
    transform.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    buffer = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    source = DataBlob(len(data), buffer)
    result = DataBlob()
    try:
        # CRYPTPROTECT_UI_FORBIDDEN. The default scope is the current user.
        if not transform(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(result)):
            raise ctypes.WinError(ctypes.get_last_error())
        return ctypes.string_at(result.data, result.size)
    finally:
        ctypes.memset(buffer, 0, len(buffer))
        if result.data:
            ctypes.memset(result.data, 0, result.size)
            kernel32.LocalFree(result.data)

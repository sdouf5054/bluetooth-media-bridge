"""
single_instance.py — Windows Named Mutex + Event 기반 단일 인스턴스 관리.
"""

import ctypes

MUTEX_NAME = "Global\\BluetoothMediaBridge_SingleInstance"
EVENT_NAME = "Global\\BluetoothMediaBridge_ShowWindow"


def acquire_mutex():
    """뮤텍스 획득 시도. 이미 실행 중이면 None 반환, 성공 시 handle 반환."""
    handle = ctypes.windll.kernel32.CreateMutexW(None, False, MUTEX_NAME)
    last_err = ctypes.windll.kernel32.GetLastError()
    if last_err == 183:  # ERROR_ALREADY_EXISTS
        ctypes.windll.kernel32.CloseHandle(handle)
        return None
    return handle


def signal_existing_instance():
    """이미 실행 중인 인스턴스에게 창 표시 신호 전달."""
    handle = ctypes.windll.kernel32.OpenEventW(
        0x0002, False, EVENT_NAME  # EVENT_MODIFY_STATE
    )
    if handle:
        ctypes.windll.kernel32.SetEvent(handle)
        ctypes.windll.kernel32.CloseHandle(handle)

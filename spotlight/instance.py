"""Single instance with takeover: a new launch asks the running one to quit, then replaces it."""

from __future__ import annotations

import ctypes
import getpass
import logging
import os
import time
from ctypes import wintypes
from pathlib import Path

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtNetwork import QLocalServer, QLocalSocket

from .winapi import kernel32

log = logging.getLogger(__name__)

_PROCESS_TERMINATE = 0x0001
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

_ERROR_ALREADY_EXISTS = 183

kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
kernel32.CreateMutexW.restype = wintypes.HANDLE
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
kernel32.TerminateProcess.restype = wintypes.BOOL
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

_PYTHON_STEMS = {"python", "pythonw"}


def default_server_name() -> str:
    return f"SpotlightLauncher-{getpass.getuser()}"


def _terminate_if_python(pid: int) -> bool:
    """Last resort for an instance that ignores the quit request. Only kills python processes."""
    handle = kernel32.OpenProcess(_PROCESS_TERMINATE | _PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return False
    try:
        size = wintypes.DWORD(1024)
        buffer = ctypes.create_unicode_buffer(size.value)
        if not kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return False
        if Path(buffer.value).stem.lower() not in _PYTHON_STEMS:
            log.warning("Refusing to terminate pid %s: %s is not a Python interpreter", pid, buffer.value)
            return False
        return bool(kernel32.TerminateProcess(handle, 1))
    finally:
        kernel32.CloseHandle(handle)


class SingleInstance(QObject):
    """The running instance. `quit_requested` fires when a newer launch wants to replace it.

    A named mutex is the lock: it vanishes by itself when the owner dies, however it dies.
    (QLocalServer cannot be the lock: on Windows a second server may listen on the same name.)
    A local server only carries the "please quit" message and the owner's pid.
    """

    quit_requested = pyqtSignal()

    def __init__(self, name: str | None = None, parent: QObject | None = None):
        super().__init__(parent)
        self._name = name or default_server_name()
        self._mutex = None
        self._server = QLocalServer(self)
        self._server.newConnection.connect(self._on_connection)

    def acquire(self, timeout_s: float = 5.0) -> bool:
        """Become the running instance, replacing the previous one if there is one."""
        if not self._take_mutex():
            log.info("Another instance is running, asking it to quit")
            pid = self._ask_to_quit()
            if not self._wait_for_mutex(timeout_s):
                if not (pid and _terminate_if_python(pid) and self._wait_for_mutex(2.0)):
                    return False
                log.warning("The previous instance (pid %s) did not quit in time and was terminated", pid)
        QLocalServer.removeServer(self._name)
        if not self._server.listen(self._name):
            log.warning("Cannot listen on %s: a newer launch will not be able to replace this instance", self._name)
        return True

    def _take_mutex(self) -> bool:
        handle = kernel32.CreateMutexW(None, False, f"Local\\{self._name}")
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        if ctypes.get_last_error() == _ERROR_ALREADY_EXISTS:
            kernel32.CloseHandle(handle)
            return False
        self._mutex = handle  # kept open for the life of the process
        return True

    def _wait_for_mutex(self, timeout_s: float) -> bool:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if self._take_mutex():
                return True
            time.sleep(0.05)
        return False

    def _ask_to_quit(self) -> int:
        """Connect to the running instance, read its pid, tell it to quit. Returns the pid or 0."""
        socket = QLocalSocket()
        socket.connectToServer(self._name)
        if not socket.waitForConnected(500):
            return 0
        pid = 0
        if socket.waitForReadyRead(500):
            greeting = bytes(socket.readLine()).decode("ascii", errors="ignore").strip()
            pid = int(greeting) if greeting.isdigit() else 0
        socket.write(b"quit\n")
        socket.flush()
        socket.waitForBytesWritten(500)
        socket.disconnectFromServer()
        return pid

    def _on_connection(self) -> None:
        connection = self._server.nextPendingConnection()
        if connection is None:
            return
        connection.write(f"{os.getpid()}\n".encode("ascii"))
        connection.flush()
        connection.readyRead.connect(lambda c=connection: self._on_data(c))
        connection.disconnected.connect(connection.deleteLater)

    def _on_data(self, connection: QLocalSocket) -> None:
        if b"quit" in bytes(connection.readAll()):
            log.info("A newer instance asked us to quit")
            self.quit_requested.emit()

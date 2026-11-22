"""Serialize lifecycle actions for a local test-management instance."""

from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import stat


class LifecycleBusyError(Exception):
    """The instance is already being handled, or cannot be locked safely."""


def _lock_directory():
    if os.name == "nt":
        import ctypes
        # The machine-wide public documents directory is independent of the
        # calling user's environment and shared by local launcher processes.
        path = ctypes.create_unicode_buffer(32768)
        if ctypes.windll.shell32.SHGetFolderPathW(None, 0x002e, None, 0, path) != 0 or not path.value:
            raise LifecycleBusyError("The shared lifecycle lock directory is unavailable.")
        return Path(path.value) / "django-core-micha-test-management"
    if os.name == "posix":
        return Path("/tmp/django-core-micha-test-management")
    raise LifecycleBusyError("Lifecycle locking is unsupported on this platform.")


@contextmanager
def instance_lock(project):
    """Acquire a nonblocking OS lock; the kernel releases it if the process exits."""
    directory = _lock_directory()
    digest = hashlib.sha256(project.encode("utf-8")).hexdigest()
    path = directory / (digest + ".lock")
    fd = None
    locked = False
    try:
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        if directory.is_symlink() or path.is_symlink():
            raise LifecycleBusyError("The lifecycle lock path is unsafe.")
        if os.name == "posix":
            info = directory.stat()
            if (not stat.S_ISDIR(info.st_mode) or info.st_uid not in (0, os.geteuid()) or
                    stat.S_IMODE(info.st_mode) & 0o022):
                raise LifecycleBusyError("The lifecycle lock directory has unsafe ownership or permissions.")
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(path, flags, 0o600)
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise LifecycleBusyError("The lifecycle lock is not a regular file.")
        if os.fstat(fd).st_size == 0:
            os.write(fd, b"0")
        os.lseek(fd, 0, os.SEEK_SET)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        elif os.name == "posix":
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        else:
            raise LifecycleBusyError("Lifecycle locking is unsupported on this platform.")
        locked = True
    except (OSError, ValueError) as exc:
        if fd is not None:
            os.close(fd)
        raise LifecycleBusyError("The instance is busy or its lifecycle lock is unavailable.") from exc
    except BaseException:
        if fd is not None:
            os.close(fd)
        raise
    try:
        yield
    finally:
        if locked:
            try:
                os.lseek(fd, 0, os.SEEK_SET)
                if os.name == "nt":
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)

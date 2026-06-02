"""Safe file I/O helpers for production write paths.

Provides three primitives used across the nowplaying package and scripts:

- ``safe_write_bytes(path, data)`` — write bytes without following symlinks.
- ``safe_read_bytes(path, *, max_bytes)`` — read bytes without following symlinks.
- ``is_safe_under(root, candidate)`` — verify a path resolves inside a root.

All helpers are intentionally minimal: no logging, no retry — callers handle
exceptions and log at the appropriate level for their context.
"""
from __future__ import annotations

import errno
import os
import stat
from pathlib import Path

_DEFAULT_MAX_BYTES = 16 * 1024 * 1024  # 16 MiB


def safe_write_bytes(path: Path | str, data: bytes) -> None:
    """Write *data* to *path*, refusing to follow symlinks.

    On POSIX systems the write is performed via ``os.open`` with
    ``O_NOFOLLOW`` so the kernel rejects symlinks atomically (race-free).
    On non-POSIX platforms (Windows) a pre-check via ``Path.is_symlink()``
    is used instead — it's non-atomic but acceptable for the Pi deployment
    target of this project.

    Raises ``OSError(errno.ELOOP, ...)`` if *path* (or its parent directory)
    is a symlink. Other ``OSError`` subclasses propagate as usual.

    Callers that already wrap their write sites in ``try/except OSError``
    (e.g. ``art_cache._write_art``, ``wiki.store_summary``) will catch the
    symlink error there; callers without a try/except (e.g. scripts) will
    have the exception bubble up and abort naturally.
    """
    p = Path(path)
    if hasattr(os, "O_NOFOLLOW"):
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW
        try:
            fd = os.open(p, flags, 0o666)  # skylos: ignore SKY-D215 — Why: this IS the safe-write helper; O_NOFOLLOW is the mitigation, not a new taint source
        except OSError as e:
            if e.errno in (errno.ELOOP, errno.EMLINK):
                raise OSError(
                    errno.ELOOP,
                    f"safe_write_bytes: refusing to write through symlink: {p}",
                    str(p),
                ) from e
            raise
        try:
            os.write(fd, data)
        finally:
            os.close(fd)
    else:
        # Non-POSIX fallback: pre-check for symlink (non-atomic).
        if p.is_symlink() or p.parent.is_symlink():
            raise OSError(
                errno.ELOOP,
                f"safe_write_bytes: refusing to write through symlink: {p}",
                str(p),
            )
        p.write_bytes(data)  # skylos: ignore SKY-D215 — Why: non-POSIX fallback inside the safe-write helper; is_symlink() pre-check above is the mitigation


def safe_read_bytes(path: Path | str, *, max_bytes: int = _DEFAULT_MAX_BYTES) -> bytes:
    """Read *path*, refusing to follow symlinks and enforcing a size cap.

    On POSIX systems the file is opened via ``os.open`` with ``O_RDONLY |
    O_NOFOLLOW`` so the kernel rejects symlinks atomically (race-free).  An
    ``fstat`` confirms the FD refers to a regular file (not a FIFO, device, or
    directory) before any data is read.  At most *max_bytes* bytes are
    returned; if the file is larger an ``OSError(errno.EFBIG)`` is raised.

    On non-POSIX platforms (Windows) a pre-check via ``Path.is_symlink()`` is
    used instead — non-atomic but acceptable for the Pi deployment target.

    Raises:
        ``OSError(errno.ELOOP, ...)`` if *path* is a symlink.
        ``OSError(errno.EISDIR, ...)`` if *path* is not a regular file.
        ``OSError(errno.EFBIG, ...)`` if the file exceeds *max_bytes*.
    """
    p = Path(path)
    if hasattr(os, "O_NOFOLLOW"):
        try:
            # O_NONBLOCK so opening a FIFO/device returns immediately instead of
            # blocking forever waiting for a writer — the S_ISREG check below
            # then rejects it. Without it a FIFO path hangs the caller (and the
            # test suite). No-op for regular files. skylos: ignore SKY-D325 — Why: this IS the safe-read helper; O_NOFOLLOW is the mitigation, not a new taint source
            fd = os.open(p, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except OSError as e:
            if e.errno in (errno.ELOOP, errno.EMLINK):
                raise OSError(
                    errno.ELOOP,
                    f"safe_read_bytes: refusing to read through symlink: {p}",
                    str(p),
                ) from e
            raise
        try:
            st = os.fstat(fd)
            if not stat.S_ISREG(st.st_mode):
                raise OSError(
                    errno.EISDIR,
                    f"safe_read_bytes: path is not a regular file: {p}",
                    str(p),
                )
            if st.st_size > max_bytes:
                raise OSError(
                    errno.EFBIG,
                    f"safe_read_bytes: file exceeds max_bytes={max_bytes}: {p}",
                    str(p),
                )
            return os.read(fd, max_bytes)  # skylos: ignore SKY-P401 — Why: caller enforces max_bytes via the EFBIG guard above; full-buffer read up to that limit is the documented API contract
        finally:
            os.close(fd)
    else:
        # Non-POSIX fallback: pre-check for symlink (non-atomic).
        if p.is_symlink():
            raise OSError(
                errno.ELOOP,
                f"safe_read_bytes: refusing to read through symlink: {p}",
                str(p),
            )
        size = p.stat().st_size
        if size > max_bytes:
            raise OSError(
                errno.EFBIG,
                f"safe_read_bytes: file exceeds max_bytes={max_bytes}: {p}",
                str(p),
            )
        return p.read_bytes()  # skylos: ignore SKY-D325 — Why: non-POSIX fallback inside the safe-read helper; is_symlink() pre-check above is the mitigation


def is_safe_under(root: Path | str, candidate: Path | str) -> bool:
    """Return True when *candidate* resolves to a path inside *root*.

    Both paths are resolved with ``Path.resolve()`` (follows symlinks,
    makes absolute) before the containment check so ``..`` sequences and
    relative paths are handled correctly.

    Returns False if *candidate* resolves outside *root*. Callers decide
    the appropriate response (raise, log, skip).
    """
    root_resolved = Path(root).resolve()
    candidate_resolved = Path(candidate).resolve()
    return candidate_resolved.is_relative_to(root_resolved)

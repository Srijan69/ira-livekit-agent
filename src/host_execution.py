"""Cooperative desktop ownership shared across IRA sessions and processes."""

import asyncio
import errno
import json
import os
from pathlib import Path

from livekit.agents import ToolError


class HostActionTimeout(ToolError):  # noqa: N818 - matches the timeout status category
    """A bounded caller result, distinct from the lifetime of native work."""

    def __init__(self, *, effect_started: bool):
        self.effect_started = effect_started
        self.retryable = not effect_started
        super().__init__(
            json.dumps(
                {
                    "status": (
                        "host_action_timeout"
                        if effect_started
                        else "host_queue_timeout"
                    ),
                    "retryable": self.retryable,
                    "effect_started": effect_started,
                    "outcome": "uncertain" if effect_started else "not_started",
                    "model_instruction": (
                        "The worker may still be running. Do not repeat the action or "
                        "claim success; inspect the outcome after host ownership settles."
                        if effect_started
                        else "The action did not start. Retry only while the same "
                        "human request remains authorized and host ownership is available."
                    ),
                },
                separators=(",", ":"),
            )
        )


class HostExecutionLock:
    """An async lock backed by a one-byte OS lock, released on process exit."""

    def __init__(self, path):
        self.path = Path(path)
        self._local = asyncio.Lock()
        self._file = None

    async def acquire(self):
        await self._local.acquire()
        handle = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            handle = self.path.open("a+b")
            if handle.seek(0, 2) == 0:
                handle.write(b"0")
                handle.flush()
            while True:
                handle.seek(0)
                try:
                    if os.name == "nt":
                        import msvcrt

                        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl

                        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError as exc:
                    if exc.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                        raise
                    await asyncio.sleep(0.05)
                else:
                    self._file = handle
                    return True
        except BaseException:
            if handle:
                handle.close()
            self._local.release()
            raise

    def release(self):
        if self._file is None:
            raise RuntimeError("Desktop ownership is not held.")
        handle, self._file = self._file, None
        try:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()
            self._local.release()

    def locked(self):
        return self._local.locked()

    async def __aenter__(self):
        await self.acquire()
        return self

    async def __aexit__(self, *args):
        self.release()

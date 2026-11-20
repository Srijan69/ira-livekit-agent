"""One thread owns Playwright while callers retain their request context."""

import contextvars
import threading
from concurrent.futures import ThreadPoolExecutor
from functools import wraps

ACTION_TIMEOUT = 4000


class BrowserWorker:
    def __init__(self):
        self._condition = threading.Condition(threading.RLock())
        self._executor = None
        self._owner = None
        self._closing = False

    @property
    def is_owner(self):
        return threading.get_ident() == self._owner

    def run(self, fn, *args, **kwargs):
        if self.is_owner:
            return fn(*args, **kwargs)
        context = contextvars.copy_context()
        with self._condition:
            self._condition.wait_for(lambda: not self._closing)
            if self._executor is None:
                self._executor = ThreadPoolExecutor(
                    max_workers=1, thread_name_prefix="ira-playwright"
                )

            def execute():
                self._owner = threading.get_ident()
                return context.run(fn, *args, **kwargs)

            future = self._executor.submit(execute)
        return future.result()

    def close(self):
        if self.is_owner:
            raise RuntimeError("The browser worker must close from its caller thread.")
        with self._condition:
            self._condition.wait_for(lambda: not self._closing)
            executor = self._executor
            if executor is None:
                return
            self._closing = True
        try:
            executor.shutdown(wait=True)
        finally:
            with self._condition:
                self._executor = None
                self._owner = None
                self._closing = False
                self._condition.notify_all()


def browser_call(fn):
    @wraps(fn)
    def invoke(self, *args, **kwargs):
        return self._worker.run(fn, self, *args, **kwargs)

    return invoke

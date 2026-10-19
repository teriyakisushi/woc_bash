"""终端 loading 指示器。

只依赖标准库；只在 stderr 是 tty 时启用，管道/测试环境自动静默。
用 daemon 线程刷新一行，`stop()` 清掉整行；不会隐藏光标，异常/中断下也能恢复。
"""

import os
import sys
import threading
import time

_FRAMES = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")


class Spinner:
    """一行式 loading：`[woc] Thinking ⠋ 1.2s`。"""

    def __init__(self, label, stream=None, enabled=True, interval=0.1):
        self.label = label
        self.stream = stream or sys.stderr
        self.interval = interval
        self.enabled = bool(enabled) and not os.environ.get("WOC_NO_SPINNER")
        if self.enabled:
            try:
                self.enabled = self.stream.isatty()
            except Exception:
                self.enabled = False
        self._stop = threading.Event()
        self._thread = None
        self._started = 0.0
        self._stopped = False
        self._lock = threading.Lock()

    def start(self):
        if not self.enabled or self._thread is not None:
            return self
        self._started = time.monotonic()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return self

    def _run(self):
        index = 0
        while not self._stop.wait(self.interval):
            elapsed = time.monotonic() - self._started
            frame = _FRAMES[index % len(_FRAMES)]
            index += 1
            with self._lock:
                if self._stopped:
                    return
                try:
                    self.stream.write(f"\r{self.label} {frame} {elapsed:4.1f}s")
                    self.stream.flush()
                except (OSError, ValueError):
                    return

    def stop(self):
        if not self.enabled or self._stopped or self._thread is None:
            return
        self._stopped = True
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
        with self._lock:
            try:
                self.stream.write("\r\x1b[K")
                self.stream.flush()
            except (OSError, ValueError):
                pass

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.stop()
        return False

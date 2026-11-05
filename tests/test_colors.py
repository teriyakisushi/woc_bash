"""`woc/colors.py` 行编辑器测试。

纯函数（历史、删词边界）直接测；raw-mode 分支只在真 tty 上走，
所以用 pty 起一个子进程，像用户那样真的按键。

两个时序前提：
1. `interactive_input()` 用 TCSAFLUSH 进 raw 模式，会丢掉此刻已到达的输入，
   所以子进程必须先落到 read() 上（等 SETTLE），父进程再发键。
2. Enter 之后会清空残留字节（多行粘贴保护），所以连续两次编辑要分批发，
   中间留 STAGE —— 真人也是看到提示才敲。
超时一律 SIGKILL，避免测试挂住。
"""

import ast
import os
import select
import signal
import time
import unittest

try:
    import pty
    import tty
    from woc import colors
except ImportError as exc:          # Windows 没有 termios / tty
    raise unittest.SkipTest("需要 POSIX 终端模块：%s" % exc)

CR = bytes([13])
ESC = bytes([27])
LEFT = ESC + b"[D"
UP = ESC + b"[A"
DOWN = ESC + b"[B"
SETTLE = 0.25
STAGE = 0.35


def ctrl(code):
    return bytes([code])


class WordStartTest(unittest.TestCase):
    def test_stops_at_word_boundary(self):
        self.assertEqual(colors._word_start(list("ab cd"), 5), 3)
        self.assertEqual(colors._word_start(list("ab cd  "), 8), 3)
        self.assertEqual(colors._word_start(list("ab cd"), 2), 0)
        self.assertEqual(colors._word_start([], 0), 0)


class HistoryTest(unittest.TestCase):
    def setUp(self):
        colors._HISTORY.clear()

    def tearDown(self):
        colors._HISTORY.clear()

    def test_skips_empty_and_adjacent_duplicates(self):
        colors._remember("")
        colors._remember("ls -l")
        colors._remember("ls -l")
        colors._remember("pwd")
        self.assertEqual(colors._HISTORY, ["ls -l", "pwd"])

    def test_is_bounded(self):
        for index in range(colors._HISTORY_LIMIT + 10):
            colors._remember("cmd %d" % index)
        self.assertEqual(len(colors._HISTORY), colors._HISTORY_LIMIT)
        self.assertEqual(colors._HISTORY[-1], "cmd 209")


class EditorTest(unittest.TestCase):
    """真 pty 上按字节驱动 `interactive_input()`。"""

    def setUp(self):
        colors._HISTORY.clear()

    def tearDown(self):
        colors._HISTORY.clear()

    @staticmethod
    def _read(fd, timeout=0.1):
        ready, _, _ = select.select([fd], [], [], timeout)
        if not ready:
            return b""
        try:
            return os.read(fd, 4096)
        except OSError:
            return b""

    @staticmethod
    def _reap(pid, timeout=2.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            done, _ = os.waitpid(pid, os.WNOHANG)
            if done:
                return
            time.sleep(0.02)
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
        os.waitpid(pid, 0)

    def run_editor(self, chunks, calls=1):
        """在 pty 里跑 `calls` 次编辑；`chunks` 分批写，模拟"看到提示再敲键"。"""
        if isinstance(chunks, (bytes, bytearray)):
            chunks = [chunks]
        master, slave = pty.openpty()
        pid = os.fork()
        if pid == 0:
            os.close(master)
            os.setsid()
            for fd in (0, 1, 2):
                os.dup2(slave, fd)
            try:
                tty.setraw(0)
                results = [colors.interactive_input("> ") for _ in range(calls)]
                os.write(1, ("\nRESULT=" + repr(results) + "\n").encode())
            except BaseException as exc:            # noqa: BLE001
                try:
                    os.write(1, ("\nEXC=" + repr(exc) + "\n").encode())
                except OSError:
                    pass
            finally:
                os._exit(0)

        os.close(slave)
        time.sleep(SETTLE)
        buffer = b""
        try:
            last = len(chunks) - 1
            for index, chunk in enumerate(chunks):
                if index:
                    time.sleep(STAGE)
                os.write(master, chunk)
                if index < last:
                    # 中间批次只要确认对方吃进去了；等结果是下一批的事，
                    # 在这里死等会把每个用例拖到超时。
                    buffer += self._read(master, timeout=0.05)
                    continue
                deadline = time.time() + 3
                while time.time() < deadline:
                    buffer += self._read(master)
                    if b"RESULT=" in buffer or b"EXC=" in buffer:
                        break
        finally:
            os.close(master)
            self._reap(pid)

        text = buffer.decode("utf-8", "replace")
        lines = [item for item in text.splitlines()
                 if item.startswith("RESULT=") or item.startswith("EXC=")]
        self.assertTrue(lines, "编辑器没有返回：%r" % text[-300:])
        line = lines[-1]
        value = ast.literal_eval(line.split("=", 1)[1])
        if line.startswith("EXC="):
            self.fail("编辑器抛异常：%r" % (value,))
        return tuple(value)

    def test_ctrl_u_clears_to_start(self):
        self.assertEqual(self.run_editor(b"ab" + ctrl(21) + b"c" + CR), ("c",))

    def test_ctrl_a_moves_to_start(self):
        self.assertEqual(self.run_editor(b"ab" + ctrl(1) + b"c" + CR), ("cab",))

    def test_ctrl_e_moves_to_end(self):
        payload = b"ab" + LEFT + LEFT + ctrl(5) + b"c" + CR
        self.assertEqual(self.run_editor(payload), ("abc",))

    def test_ctrl_w_deletes_previous_word(self):
        self.assertEqual(self.run_editor(b"ab cd" + ctrl(23) + CR), ("ab ",))

    def test_ctrl_k_deletes_to_end(self):
        self.assertEqual(self.run_editor(b"abc" + LEFT + ctrl(11) + CR), ("ab",))

    def test_unknown_control_char_never_enters_text(self):
        self.assertEqual(self.run_editor(b"a" + ctrl(2) + b"b" + CR), ("ab",))

    def test_arrow_left_moves_cursor(self):
        self.assertEqual(self.run_editor(b"ab" + LEFT + b"Z" + CR), ("aZb",))

    def test_up_recalls_previous_line(self):
        self.assertEqual(self.run_editor([b"first" + CR, UP + CR], calls=2),
                         ("first", "first"))

    def test_down_restores_the_draft(self):
        payload = [b"first" + CR, UP + DOWN + b"x" + CR]
        self.assertEqual(self.run_editor(payload, calls=2), ("first", "x"))

    def test_multiline_paste_keeps_only_first_line(self):
        self.assertEqual(self.run_editor(b"l1" + CR + b"l2" + CR), ("l1",))

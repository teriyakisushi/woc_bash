"""agentic loop 的回归测试。

默认**完全离线**，不碰网络、不读用户配置：

    python3 -m unittest discover -s tests

真实 API 的 `#CHAT:` 合规测试默认跳过，需要显式开启：

    WOC_LIVE_TEST=1 python3 -m unittest tests.test_loop.LiveChatMarkerTest

这一组测试守的是一个已经踩过的坑：模型会模仿 assistant 历史里的格式，所以
`#CHAT:` 标记必须留在历史里（见 docs/DESIGN-agentic-loop.md §8.1）。
"""

import io
import os
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from woc import loop as L
from woc.exec import syntax_error
from woc.llm import StreamFilter, StreamMode
from woc.ui import Spinner

BASE = {"enabled": True, "max_steps": 5, "total_chars": 24000, "auto_safe": False}


class FakeHarness:
    """替换 loop 模块里的 LLM / 执行 / 确认 / 打印。"""

    def __init__(self, replies, outputs=None):
        self.replies = list(replies)
        self.calls = 0
        self.executed = []
        self.outputs = outputs or {}
        self.stderr = io.StringIO()
        self.stdout = io.StringIO()
        self.raw_stderr = io.StringIO()

    def __enter__(self):
        self._saved = (L.call_llm_messages, L.run_command_capture,
                       L.read_key, L.interactive_input, L.eprint)
        L.call_llm_messages = self.llm
        L.run_command_capture = self.capture
        L.read_key = lambda prompt="": ""          # 默认按 Enter 执行
        L.interactive_input = lambda prompt="", **kw: ""
        L.eprint = self.eprint
        self._redirect = redirect_stdout(self.stdout)
        self._redirect.__enter__()
        self._redirect_err = redirect_stderr(self.raw_stderr)
        self._redirect_err.__enter__()
        return self

    def __exit__(self, *exc):
        self._redirect_err.__exit__(*exc)
        self._redirect.__exit__(*exc)
        (L.call_llm_messages, L.run_command_capture,
         L.read_key, L.interactive_input, L.eprint) = self._saved
        return False

    def eprint(self, text="", *styles):
        self.stderr.write(str(text) + "\n")

    def llm(self, messages, cfg, on_chunk=None, on_mode=None):
        reply = self.replies[self.calls]
        self.calls += 1
        if on_chunk:
            on_chunk(reply)
        if on_mode:
            on_mode(StreamMode.COMMAND)
        return reply

    def capture(self, cmd, max_output=8000, timeout=120):
        self.executed.append(cmd)
        value = self.outputs.get(cmd, "ok\n")
        if isinstance(value, tuple):
            return value
        return value, "", 0

    def run(self, messages, loop_cfg=None, enabled=True, extra=None):
        cfg = {"loop": loop_cfg or BASE, "prefix": {"alias": "woc"}}
        if extra:
            cfg.update(extra)
        L.run_turn(messages, cfg, alias="woc", capture=False, loop_enabled=enabled)
        return messages


class RiskTierTest(unittest.TestCase):
    def test_safe_commands(self):
        for cmd in ("ls -l", "cat README.md", "grep -n loop README.md", "wc -l x"):
            self.assertEqual(L.risk_tier(cmd), "safe", cmd)

    def test_unbounded_commands_need_confirmation(self):
        for cmd in ("ls | head", "ls -l; rm x", "cat a | xargs rm",
                    "find . -exec rm {} +", "sort -o /tmp/x file",
                    "awk '{print $1}' f", "sed -i s/a/b/ f", "echo $(whoami)"):
            self.assertEqual(L.risk_tier(cmd), "confirm", cmd)

    def test_dangerous_commands_denied(self):
        for cmd in ("rm -rf /", "sudo -i", "sudo -H -i", "shutdown -h now"):
            self.assertEqual(L.risk_tier(cmd), "deny", cmd)

    def test_grep_su_is_not_root_shell(self):
        self.assertFalse(L.is_root_shell("grep su README.md"))


class StreamFilterTest(unittest.TestCase):
    def stream(self, text):
        flt = StreamFilter()
        out = []
        for char in text:
            out.append(flt.feed(char))
        out.append(flt.finish())
        return "".join(out), flt.mode

    def test_step_marker(self):
        emitted, mode = self.stream("#STEP: ls -l")
        self.assertEqual(emitted.strip(), "ls -l")
        self.assertEqual(mode, StreamMode.COMMAND)

    def test_chat_marker(self):
        emitted, mode = self.stream("#CHAT: 你好")
        self.assertEqual(emitted.strip(), "你好")
        self.assertEqual(mode, StreamMode.CHAT)

    def test_plain_command(self):
        emitted, mode = self.stream("ls -l")
        self.assertEqual(emitted, "ls -l")
        self.assertEqual(mode, StreamMode.COMMAND)

    def test_marker_prefix_is_buffered(self):
        flt = StreamFilter()
        self.assertEqual(flt.feed("#S"), "")
        self.assertEqual(flt.feed("TEP: "), " ")
        self.assertEqual(flt.feed("ls"), "ls")


class LoopDriverTest(unittest.TestCase):
    def test_step_then_chat(self):
        with FakeHarness(["#STEP: ls -l", "#CHAT: 完成"]) as harness:
            messages = harness.run([{"role": "user", "content": "q"}])
        self.assertEqual(harness.calls, 2)
        self.assertEqual(harness.executed, ["ls -l"])
        self.assertEqual([m["role"] for m in messages],
                         ["user", "assistant", "user", "assistant"])
        self.assertEqual(messages[1]["content"], "#STEP: ls -l")
        self.assertEqual(messages[3]["content"], "#CHAT: 完成")

    def test_plain_command_ends_turn(self):
        with FakeHarness(["ls -l"]) as harness:
            messages = harness.run([{"role": "user", "content": "q"}])
        self.assertEqual(harness.calls, 1)
        self.assertEqual(harness.executed, ["ls -l"])
        self.assertEqual(len(messages), 3)

    def test_max_steps(self):
        cfg = dict(BASE, max_steps=3)
        replies = ["#STEP: echo %d" % i for i in range(5)]
        with FakeHarness(replies) as harness:
            harness.run([{"role": "user", "content": "q"}], loop_cfg=cfg)
        self.assertEqual(harness.calls, 3)
        self.assertEqual(len(harness.executed), 3)
        self.assertIn("步上限", harness.stderr.getvalue())

    def test_repeated_command_aborts(self):
        with FakeHarness(["#STEP: ls -l", "#STEP: ls -l"]) as harness:
            harness.run([{"role": "user", "content": "q"}])
        self.assertEqual(harness.calls, 2)
        self.assertEqual(harness.executed, ["ls -l"])

    def test_dangerous_step_not_executed(self):
        with FakeHarness(["#STEP: rm -rf /", "#CHAT: 好的"]) as harness:
            messages = harness.run([{"role": "user", "content": "q"}])
        self.assertEqual(harness.executed, [])
        self.assertIn("126", messages[2]["content"])

    def test_budget_stops_loop(self):
        cfg = dict(BASE, total_chars=1)
        with FakeHarness(["#STEP: ls -l", "#STEP: ls"]) as harness:
            harness.run([{"role": "user", "content": "q"}], loop_cfg=cfg)
        self.assertEqual(harness.calls, 1)

    def test_disabled_loop_ignores_step_marker(self):
        with FakeHarness(["#STEP: ls -l", "#CHAT: x"]) as harness:
            harness.run([{"role": "user", "content": "q"}], enabled=False)
        self.assertEqual(harness.calls, 1)
        self.assertEqual(harness.executed, ["ls -l"])


class ChatMarkerHistoryTest(unittest.TestCase):
    """回归：历史里的 #CHAT: 不能丢（丢了模型就会连锁遗忘）。"""

    def test_marker_is_kept_in_history(self):
        with FakeHarness(["#CHAT: 这是回答"]) as harness:
            messages = harness.run([{"role": "user", "content": "q"}])
        assistant = [m for m in messages if m["role"] == "assistant"]
        self.assertEqual(assistant[-1]["content"], "#CHAT: 这是回答")

    def test_marker_kept_across_turns(self):
        with FakeHarness(["#CHAT: 第一轮", "#CHAT: 第二轮"]) as harness:
            messages = [{"role": "user", "content": "q1"}]
            harness.run(messages)
            messages.append({"role": "user", "content": "q2"})
            harness.run(messages)
        assistant = [m["content"] for m in messages if m["role"] == "assistant"]
        self.assertEqual(assistant, ["#CHAT: 第一轮", "#CHAT: 第二轮"])

    def test_step_marker_is_kept(self):
        """#STEP: 也要留在历史里；模型会模仿自己上一轮的格式。"""
        with FakeHarness(["#STEP: ls -l", "#CHAT: 完成"]) as harness:
            messages = harness.run([{"role": "user", "content": "q"}])
        self.assertEqual(messages[1]["content"], "#STEP: ls -l")


class ProseHintTest(unittest.TestCase):
    """可观测性：疑似把回答当命令执行时，给一行提示。"""

    PROSE = "争议大，很大程度上是因为它同时踩了好几个雷。"

    def test_hint_when_exit_127_with_chinese(self):
        outputs = {self.PROSE: ("", "bash: line 1: 争议大: command not found", 127)}
        with FakeHarness([self.PROSE], outputs=outputs) as harness:
            harness.run([{"role": "user", "content": "q"}])
        self.assertIn("漏了 #CHAT: 标记", harness.stderr.getvalue())

    def test_no_hint_for_english_command(self):
        cmd = "definitely-not-a-command"
        outputs = {cmd: ("", "bash: line 1: command not found", 127)}
        with FakeHarness([cmd], outputs=outputs) as harness:
            harness.run([{"role": "user", "content": "q"}])
        self.assertNotIn("漏了 #CHAT: 标记", harness.stderr.getvalue())


LIVE_SETS = {
    "systemd": ["什么是 systemd？", "它和传统 init 有什么关系？",
                "那 SysV init 具体怎么工作？", "systemctl 常用命令有哪些？",
                "为什么社区对 systemd 争议大？", "用一句话总结"],
    "docker": ["docker 和虚拟机的区别？", "为什么 docker 启动这么快？",
               "镜像层是什么？", "容器里跑多个进程好不好？",
               "什么是 OCI？", "什么时候不该用 docker？"],
}


@unittest.skipUnless(os.environ.get("WOC_LIVE_TEST") == "1",
                     "真实 API 测试需要 WOC_LIVE_TEST=1")
class LiveChatMarkerTest(unittest.TestCase):
    """真实 API 下的 #CHAT: 合规率（慢、花钱，默认跳过）。"""

    def test_compliance(self):
        from woc.config import load_config, ls_on_start, prefix
        from woc.context import messages_prefix
        from woc.llm import call_llm_messages
        from woc.prompts import LOOP_HINT, MULTI_TURN_HINT, render_system_prompt

        cfg = load_config()
        total = 0
        marked = 0
        for name, questions in LIVE_SETS.items():
            messages = messages_prefix(
                multi_turn=True,
                system_prompt=render_system_prompt(prefix(cfg, "alias")),
                multi_turn_hint=MULTI_TURN_HINT + "\n" + LOOP_HINT,
                with_listing=ls_on_start(cfg),
            )
            for turn, question in enumerate(questions, 1):
                messages.append({"role": "user", "content": question})
                text = call_llm_messages(messages, cfg).strip()
                has_marker = text.startswith("#CHAT:")
                marked += has_marker
                total += 1
                body = text[len("#CHAT:"):].strip() if has_marker else text
                # 无论是否合规，都按修复后的方式写入历史，模拟真实会话。
                messages.append({"role": "assistant", "content": "#CHAT: " + body})
                if not has_marker:
                    print("  MISS %s turn%d: %s" % (name, turn, text[:50]))
        print("  合规: %d/%d" % (marked, total))
        self.assertGreaterEqual(marked, total - 1,
                                "合规率过低：%d/%d，检查历史是否保留了 #CHAT:" % (marked, total))


if __name__ == "__main__":
    unittest.main(verbosity=2)


class OutputFoldTest(unittest.TestCase):
    LONG = "\n".join("line %d" % i for i in range(100)) + "\n"

    def test_long_output_is_folded(self):
        cmd = "seq 100"
        with FakeHarness([cmd], outputs={cmd: (self.LONG, "", 0)}) as harness:
            harness.run([{"role": "user", "content": "q"}])
        stdout = harness.stdout.getvalue()
        self.assertIn("已折叠", stdout)
        self.assertIn("line 0", stdout)
        self.assertIn("line 99", stdout)
        self.assertNotIn("line 50", stdout)

    def test_short_output_not_folded(self):
        cmd = "ls"
        with FakeHarness([cmd], outputs={cmd: ("a\nb\n", "", 0)}) as harness:
            harness.run([{"role": "user", "content": "q"}])
        self.assertNotIn("已折叠", harness.stdout.getvalue())

    def test_failed_command_never_folded(self):
        cmd = "cat missing"
        with FakeHarness([cmd], outputs={cmd: (self.LONG, "boom\n", 1)}) as harness:
            harness.run([{"role": "user", "content": "q"}])
        self.assertNotIn("已折叠", harness.stdout.getvalue())

    def test_expand_last_output(self):
        cmd = "seq 100"
        with FakeHarness([cmd], outputs={cmd: (self.LONG, "", 0)}) as harness:
            harness.run([{"role": "user", "content": "q"}])
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            self.assertTrue(L.expand_last_output())
        self.assertIn("line 50", buffer.getvalue())
        self.assertEqual(L.last_command(), cmd)


class DenyReasonTest(unittest.TestCase):
    def test_reason_is_reported(self):
        with FakeHarness(["rm -rf /"]) as harness:
            harness.run([{"role": "user", "content": "q"}])
        self.assertIn("递归删除根目录", harness.stderr.getvalue())

    def test_reason_goes_back_to_model(self):
        with FakeHarness(["mkfs.ext4 /dev/sda1"]) as harness:
            messages = harness.run([{"role": "user", "content": "q"}])
        self.assertIn("格式化文件系统", messages[2]["content"])


class StickyLoopTest(unittest.TestCase):
    """粘性续步：本回合出现过 #STEP: 后，裸命令也继续，直到 #CHAT:。"""

    def test_plain_command_after_step_continues(self):
        with FakeHarness(["#STEP: ls -l", "pwd", "#CHAT: 完成"]) as harness:
            messages = harness.run([{"role": "user", "content": "q"}])
        self.assertEqual(harness.calls, 3)
        self.assertEqual(harness.executed, ["ls -l", "pwd"])
        self.assertEqual(messages[-1]["content"], "#CHAT: 完成")

    def test_plain_command_without_step_still_ends_turn(self):
        with FakeHarness(["ls -l"]) as harness:
            harness.run([{"role": "user", "content": "q"}])
        self.assertEqual(harness.calls, 1)
        self.assertEqual(len(harness.executed), 1)

    def test_step_marker_is_kept_after_edit(self):
        with FakeHarness(["#STEP: ls -l", "pwd", "#CHAT: 完成"]) as harness:
            messages = harness.run([{"role": "user", "content": "q"}])
        assistant = [m["content"] for m in messages if m["role"] == "assistant"]
        self.assertEqual(assistant[0], "#STEP: ls -l")

    def test_max_steps_from_config_beyond_old_cap(self):
        cfg = dict(BASE, max_steps=10)
        replies = ["#STEP: echo %d" % i for i in range(10)]
        with FakeHarness(replies) as harness:
            harness.run([{"role": "user", "content": "q"}], loop_cfg=cfg)
        self.assertEqual(harness.calls, 10)
        self.assertEqual(len(harness.executed), 10)
        self.assertIn("10 步上限", harness.stderr.getvalue())


class ProtocolGuardTest(unittest.TestCase):
    def test_toolcall_residue_is_not_executed(self):
        replies = ["#STEP: ls -l",
                   "<｜｜DSML｜｜ calls>\n<｜｜DSML｜｜ invoke name=\"exec_result\">\n"
                   "placeholder\n</exec_result>",
                   "#CHAT: 完成"]
        with FakeHarness(replies) as harness:
            messages = harness.run([{"role": "user", "content": "q"}])
        self.assertEqual(harness.executed, ["ls -l"])
        self.assertEqual(harness.calls, 3)
        self.assertIn("工具调用", harness.stderr.getvalue())
        self.assertIn("exit_code: 1", messages[4]["content"])

    def test_dsml_tag_after_valid_command_is_rejected(self):
        # strip_dsml 会把尾巴上的 DSML 全剥掉，只剩 pwd；守卫必须看 raw 才能拦住它。
        replies = ["#STEP: ls -l", "pwd\n<｜｜DSML｜｜ parameter>", "#CHAT: 完成"]
        with FakeHarness(replies) as harness:
            harness.run([{"role": "user", "content": "q"}])
        self.assertEqual(harness.executed, ["ls -l"])
        self.assertEqual(harness.calls, 3)

    def test_syntax_error_is_not_executed(self):
        replies = ["#STEP: ls -l", "#STEP: echo 'unclosed", "#CHAT: 完成"]
        with FakeHarness(replies) as harness:
            messages = harness.run([{"role": "user", "content": "q"}])
        self.assertEqual(harness.executed, ["ls -l"])
        self.assertEqual(harness.calls, 3)
        self.assertIn("语法错误", harness.stderr.getvalue())
        self.assertIn("exit_code: 2", messages[4]["content"])

    def test_syntax_check_can_be_disabled(self):
        cfg = dict(BASE)
        with FakeHarness(["echo 'unclosed"]) as harness:
            harness.run([{"role": "user", "content": "q"}],
                        extra={"exec": {"syntax_check": False}})
        self.assertEqual(harness.executed, ["echo 'unclosed"])


class SyntaxCheckTest(unittest.TestCase):
    def test_valid_commands(self):
        for cmd in ("ls -l", "echo hi | grep h", "f=$(find . -type f | head -n1); echo $f"):
            self.assertIsNone(syntax_error(cmd), cmd)

    def test_invalid_commands(self):
        for cmd in ("ls -l</br>", "placeholder\n</exec_result>"):
            self.assertIsNotNone(syntax_error(cmd), cmd)


class SpinnerTest(unittest.TestCase):
    def test_non_tty_is_silent(self):
        stream = io.StringIO()
        spin = Spinner("test", stream=stream)
        spin.start()
        spin.stop()
        self.assertEqual(stream.getvalue(), "")

"""危险命令门禁的离线测试。

从 tests/test_loop.py 拆出来：gate 是独立模块，template-reference 分支也要跑。
"""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from woc.gate import dangerous_reason


class GateTest(unittest.TestCase):
    ALLOWED = (
        "printf '%s\\n' 'full reboot' > /tmp/issue.txt",
        "grep reboot /var/log/syslog",
        "man reboot",
        "journalctl -u reboot.target",
        'git commit -m "fix reboot"',
        "grep mkfs /proc/filesystems",
        "ls | grep mkfs",
        "rm -rf /tmp/x",
        "echo hi > /dev/null",
        "systemctl status reboot.target",
        "awk '{print \"reboot\"}'",
        "printf '%s\\n' 'mkfs.ext4 /dev/sda' > /tmp/x",
        "printf '%s\\n' 'rm -rf /' > /tmp/x",
        'git commit -m "shutdown"',
        "find . -name reboot",
    )
    DENIED = (
        "systemctl reboot",
        "sudo reboot",
        "bash -c 'reboot'",
        'sh -c "shutdown -h now"',
        "eval 'reboot'",
        "find . -exec reboot {} +",
        "xargs -0 reboot",
        "timeout 5 reboot",
        "env FOO=1 reboot",
        "nice -n 5 reboot",
        "(reboot)",
        "echo $(reboot)",
        "echo `reboot`",
        "rm -rf /",
        "rm -rf /*",
        "mkfs.ext4 /dev/sdb1",
        "dd if=/dev/zero of=/dev/sda bs=1M",
        "echo hi > /dev/sda",
        "tee /dev/sda",
        "chmod -R 777 /",
        ":(){ :|:& };:",
        "printf 'reboot' | bash",
        "sudo -u root bash -c 'reboot'",
        "systemctl start reboot.target",
        "rm -rf /tmp/..",
        "echo hi > /dev/nvme0n1",
        "timeout -s KILL 5 reboot",
        "find . -exec sh -c 'reboot' \\;",
    )

    def test_allowed_commands(self):
        for cmd in self.ALLOWED:
            self.assertIsNone(dangerous_reason(cmd), cmd)

    def test_denied_commands(self):
        for cmd in self.DENIED:
            self.assertIsNotNone(dangerous_reason(cmd), cmd)

    def test_reason_labels(self):
        self.assertEqual(dangerous_reason("rm -rf /"), "递归删除根目录")
        self.assertEqual(dangerous_reason("mkfs.ext4 /dev/sda1"), "格式化文件系统")
        self.assertEqual(dangerous_reason("systemctl reboot"), "关机 / 重启")



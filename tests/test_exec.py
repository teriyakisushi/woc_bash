"""`woc/exec.py` 的输出处理：脱敏。"""

import unittest

from woc.exec import redact_secrets


class RedactSecretsTest(unittest.TestCase):
    def test_field_names_are_replaced(self):
        for raw, want in (
            ("mysql -u root --password=hunter2", "mysql -u root --password=***"),
            ("SECRET_KEY=abcdef", "SECRET_KEY=***"),
            ("AWS_SECRET_ACCESS_KEY=AKIA123", "AWS_SECRET_ACCESS_KEY=***"),
            ("GITHUB_TOKEN=ghp_x", "GITHUB_TOKEN=***"),
            ("export DB_PASSWD: p@ss", "export DB_PASSWD: ***"),
        ):
            self.assertEqual(redact_secrets(raw), want, raw)

    def test_bearer_credentials(self):
        raw = 'curl -H "Authorization: Bearer eyJhbGciOi.abc"'
        self.assertEqual(redact_secrets(raw),
                         'curl -H "Authorization: Bearer ***"')

    def test_private_key_block(self):
        raw = chr(10).join(["-----BEGIN RSA PRIVATE KEY-----",
                            "MIIEpAIBAAKCAQEA",
                            "-----END RSA PRIVATE KEY-----"])
        self.assertEqual(
            redact_secrets(raw),
            "-----BEGIN PRIVATE KEY-----***-----END PRIVATE KEY-----")

    def test_environment_line(self):
        self.assertEqual(redact_secrets("Environment=A=1 B=2"), "Environment=***")

    def test_idempotent_and_plain_text(self):
        raw = "grep -r SECRET_KEY= . && echo done"
        once = redact_secrets(raw)
        self.assertEqual(redact_secrets(once), once)
        self.assertEqual(redact_secrets("ls -l /tmp"), "ls -l /tmp")

    def test_empty_input(self):
        self.assertEqual(redact_secrets(""), "")
        self.assertEqual(redact_secrets(None), None)


if __name__ == "__main__":
    unittest.main()

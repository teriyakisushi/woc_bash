"""模板检索的离线测试。

检索只决定"正文给谁"，永远不裁剪索引。这里守四件事：
词项降权（idf）、标签词边界、字段权重、零分不注入。
"""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from woc import context as C
from woc.context import Template


class TagMatchTest(unittest.TestCase):
    def test_ascii_tag_needs_word_boundary(self):
        self.assertFalse(C.tag_matches("log", "catalog"))
        self.assertFalse(C.tag_matches("log", "logfile"))
        self.assertTrue(C.tag_matches("log", "check the log file"))
        self.assertTrue(C.tag_matches("log", "log"))

    def test_cjk_tag_is_substring(self):
        self.assertTrue(C.tag_matches("日志", "看看日志"))
        self.assertTrue(C.tag_matches("日志", "日志"))

    def test_empty_tag(self):
        self.assertFalse(C.tag_matches("", "anything"))


class IdfTest(unittest.TestCase):
    def test_common_term_weighs_less_than_rare_term(self):
        templates = [
            Template("a", tags=("nginx",), when="查日志"),
            Template("b", tags=("磁盘",), when="查日志"),
        ]
        weights = C._term_weights(templates, {"nginx", "日志"})
        self.assertGreater(weights["nginx"], weights["日志"])
        self.assertLess(weights["日志"], 0.5)

    def test_score_prefers_distinctive_term(self):
        templates = [
            Template("a", tags=("nginx",), when="查日志"),
            Template("b", tags=("磁盘",), when="查日志"),
        ]
        weights = C._term_weights(templates, C.query_terms("nginx 日志"))
        scores = {t.id: C.score_template(t, "nginx 日志", weights) for t in templates}
        self.assertGreater(scores["a"], scores["b"])


class FieldWeightTest(unittest.TestCase):
    def test_when_beats_body(self):
        in_when = Template("a", when="nginx 日志")
        in_body = Template("b", body="nginx 日志")
        query = "nginx 日志"
        self.assertGreater(C.score_template(in_when, query, {}),
                           C.score_template(in_body, query, {}))

    def test_id_match_is_strong(self):
        tpl = Template("git-recent-changes", desc="最近的改动")
        self.assertGreaterEqual(C.score_template(tpl, "git-recent-changes", {}),
                                C.ID_WEIGHT)


class SelectBodiesTest(unittest.TestCase):
    def make(self, tid, desc, body):
        return Template(tid, desc=desc, body=body)

    def test_full_when_everything_fits(self):
        templates = [self.make("a", "nginx", "x" * 10),
                     self.make("b", "磁盘", "y" * 10)]
        selected, strategy, _scores = C.select_bodies(templates, query="无关",
                                                      budget=10000)
        self.assertEqual(strategy, "full")
        self.assertEqual([t.id for t in selected], ["a", "b"])

    def test_ranked_drops_zero_score_bodies(self):
        templates = [self.make("a", "nginx", "x" * 200),
                     self.make("b", "磁盘", "y" * 200)]
        selected, strategy, _scores = C.select_bodies(templates,
                                                      query="完全无关的问题",
                                                      budget=100)
        self.assertEqual(strategy, "ranked")
        self.assertEqual(selected, [])

    def test_ranked_keeps_matching_body(self):
        templates = [self.make("a", "nginx 日志", "x" * 200),
                     self.make("b", "磁盘", "y" * 200)]
        selected, _strategy, _scores = C.select_bodies(templates,
                                                       query="nginx",
                                                       budget=250)
        self.assertEqual([t.id for t in selected], ["a"])

    def test_ranked_is_deterministic(self):
        templates = [self.make("b", "nginx", "x" * 200),
                     self.make("a", "nginx", "y" * 200)]
        first = C.select_bodies(templates, query="nginx", budget=250)[0]
        second = C.select_bodies(templates, query="nginx", budget=250)[0]
        self.assertEqual([t.id for t in first], [t.id for t in second])
        self.assertEqual([t.id for t in first], sorted(t.id for t in first))


if __name__ == "__main__":
    unittest.main()

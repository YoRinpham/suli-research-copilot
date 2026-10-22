import unittest

from app.services.scientific import ScientificService


class ConflictWordingNegationTests(unittest.TestCase):
    def test_different_conditions_cannot_be_combined_as_conflict(self):
        self.assertFalse(ScientificService._explicit_conflict_wording(
            '两篇材料的练习时长和测量时点不同，不能直接合并为同一条件下相互矛盾的结果。'))

    def test_synthetic_source_negation_is_not_conflict(self):
        self.assertFalse(ScientificService._explicit_conflict_wording(
            '两篇材料不能直接合并成同一条件下相互矛盾的结果。'))

    def test_explicit_denials_are_not_conflict(self):
        for text in ['两篇材料并非相互矛盾。', '这些观察不构成直接冲突。', '尚无法认定两者存在直接矛盾。']:
            with self.subTest(text=text):
                self.assertFalse(ScientificService._explicit_conflict_wording(text))

    def test_asserted_chinese_conflict_remains_detected(self):
        self.assertTrue(ScientificService._explicit_conflict_wording('可比条件下，两项结果直接矛盾。'))

    def test_another_asserted_clause_is_not_hidden_by_a_denial(self):
        self.assertTrue(ScientificService._explicit_conflict_wording(
            '甲乙并非直接矛盾；但丙丁在可比条件下直接冲突。'))

    def test_unrelated_inability_does_not_hide_assertion(self):
        self.assertTrue(ScientificService._explicit_conflict_wording(
            '不能确定两者的基线，但报告中的结果相互矛盾。'))

    def test_unrelated_inability_within_clause_does_not_hide_conflict(self):
        self.assertTrue(ScientificService._explicit_conflict_wording(
            '无法招募样本而两项结果直接矛盾。'))

    def test_english_wording_retains_existing_behavior(self):
        self.assertTrue(ScientificService._explicit_conflict_wording('The results directly contradict each other.'))
        self.assertFalse(ScientificService._explicit_conflict_wording('The findings do not contradict each other.'))


if __name__ == '__main__':
    unittest.main()


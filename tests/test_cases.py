"""The 12 protocol cases. Expectations are fixed in fixtures/cases.json."""
import unittest
from kontrol.engine import run_check
from kontrol.models import CheckRequest
from tests.oracle import compare_result, load_cases


class AcceptanceCases(unittest.TestCase):
    pass


def test_case(case):
    def run(self):
        result = run_check(CheckRequest.model_validate(case['request']))
        self.assertEqual([], compare_result(case, result), f"{case['id']}: {case['title']}")
    run.__doc__ = case['title']
    return run


for fixture in load_cases():
    setattr(AcceptanceCases, 'test_' + fixture['id'], test_case(fixture))


if __name__ == '__main__':
    unittest.main(verbosity=2)

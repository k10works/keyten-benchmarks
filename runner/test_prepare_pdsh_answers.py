from __future__ import annotations

import tempfile
import unittest
from datetime import date
from pathlib import Path

from prepare_pdsh_answers import answer_tolerances, parse_answer


class PreparePdshAnswersTest(unittest.TestCase):
    def test_parses_trimmed_strings_numbers_and_dates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            answer = Path(directory) / "q1.out"
            answer.write_text(
                "name      |rows                |ratio               |day       \n"
                "alpha     |                  12|1.25                |1998-12-01\n"
                " beta     |                  -3|2e-2                |1999-01-02\n",
                encoding="utf-8",
            )
            self.assertEqual(
                parse_answer(answer),
                {
                    "name": ["alpha", " beta"],
                    "rows": [12, -3],
                    "ratio": [1.25, 0.02],
                    "day": [date(1998, 12, 1), date(1999, 1, 2)],
                },
            )
            self.assertEqual(answer_tolerances(answer), {"ratio": 0.005})

    def test_rejects_ragged_rows(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            answer = Path(directory) / "q1.out"
            answer.write_text("a|b\n1\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "expected 2 columns"):
                parse_answer(answer)


if __name__ == "__main__":
    unittest.main()

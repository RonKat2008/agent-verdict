"""Regenerate tests/golden/{questions,states}/*.json from tests/golden/_fixtures.py.

Not part of the hot path. Run via `make regen-golden` whenever a deliberate
wording or shape change to `state.build_state` / `questions.build_questions`
needs the checked-in golden files to catch up (task-2-brief.md: "so wording
changes are deliberate and reviewable").
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugin" / "hooks"))
sys.path.insert(0, str(ROOT / "tests"))

from golden._fixtures import FIXTURES, POLICY  # noqa: E402
from verdict_hot import claims as claims_mod  # noqa: E402
from verdict_hot import questions as questions_mod  # noqa: E402
from verdict_hot import state as state_mod  # noqa: E402

QUESTIONS_DIR = ROOT / "tests" / "golden" / "questions"
STATES_DIR = ROOT / "tests" / "golden" / "states"


def main() -> int:
    QUESTIONS_DIR.mkdir(parents=True, exist_ok=True)
    STATES_DIR.mkdir(parents=True, exist_ok=True)
    for name, span, final_message in FIXTURES:
        claim_tuple = claims_mod.extract_claims(final_message, POLICY)
        state, _overflow = state_mod.build_state(span, final_message, claim_tuple, POLICY)
        questions = questions_mod.build_questions(state)

        (STATES_DIR / f"{name}.json").write_text(
            json.dumps(state, sort_keys=True, indent=1, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        (QUESTIONS_DIR / f"{name}.json").write_text(
            json.dumps(questions, sort_keys=True, indent=1, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        print(f"wrote {name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

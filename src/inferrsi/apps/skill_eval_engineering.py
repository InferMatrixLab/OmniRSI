# Composed serving-parser artifact check: Benchmark task rules and vector judgment
# around the Harness sandbox replay. No expected result enters the sandbox.

import json
import os
import sys
from pathlib import Path

from inferrsi.benchmark import skill_eval_engineering as task_rules
from inferrsi.harness import artifact_sandbox


def verify(task, output):
    source, rejected = task_rules.submission(task, output)
    if rejected is not None:
        return rejected
    return task_rules.judge(artifact_sandbox.replay(source, task_rules.vector_inputs()))


def main():
    task = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    output = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
    result = verify(task, output)
    Path(os.environ["INFERRSI_RESULT_PATH"]).write_text(json.dumps(result), encoding="utf-8")
    raise SystemExit(0 if result["passed"] else 1)


if __name__ == "__main__":
    main()

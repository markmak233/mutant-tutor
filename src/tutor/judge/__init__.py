"""LLM judges of quality, never of validity (docs/design.md, roadmap 3b step 3).

- teaching.py: rates one exercise on the owner's four teaching-quality points;
- calibrate.py: compares those ratings with the owner's before any point is trusted.

    python -m tutor.judge <batch_dir> [<exercise_id> ...]
    python -m tutor.judge --calibrate [--blind DIR]
"""

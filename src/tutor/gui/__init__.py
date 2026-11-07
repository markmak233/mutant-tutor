"""The local GUI (docs/design.md, "Local GUI"): `python -m tutor.gui`.

A small Flask app on 127.0.0.1 over the same files the command line reads and writes. Files
stay the source of truth: the pages read workspaces, and later steps start the existing
commands. Solving stays in the IDE.
"""

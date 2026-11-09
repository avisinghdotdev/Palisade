"""Labelled cases the dispute pipeline is scored on, and the runners that score it.

The cases are data the test suite runs on every push (routing, no model). The runners call
Claude for real and write their results under `docs/evals/`, dated, so a reader can check
what the pipeline did without running anything.
"""

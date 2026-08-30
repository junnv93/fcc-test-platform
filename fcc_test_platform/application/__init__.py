"""Platform read surface (FE-P0d, 2026-05-27).

Cross-engineer, project-wide read model served from the **central** database —
distinct from the provider Headless API (``application.headless``) which serves
one engineer's local SQLite. coverage source = central
``coverage_by_condition_hash``; claim source = central ``active_claims``.
"""

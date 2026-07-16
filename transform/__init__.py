"""Phase 2 transform layer: parse raw filings into typed silver rows.

The parsers are pure (bytes -> dataclasses, no DB, no network) so they are
unit-testable against fixture documents; `pipeline` wires fetch + parse + load.
"""

"""Phase 4 streaming: a real-time path alongside the batch backbone.

Producers publish live events to Redpanda (Kafka API); consumers react
independently. The batch pipeline (Phases 1-3) stays the source of truth; this
layer is the fast/approximate operational view (lambda architecture).
"""

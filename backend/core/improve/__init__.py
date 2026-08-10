"""
Milli Self-Improvement subsystem.

Checkpoint 1: trace emission (trace_writer), version model fields, per-user
storage layout, retention. Later checkpoints add detectors, tuner, versioning,
benchmarks, and orchestration-native IMPROVE_* steps.

Everything in this package is additive and failure-isolated: no code here may
ever alter agent behavior, event streams, or existing logger output.
"""

# Changelog

## Unreleased — 0.2.0 local prototype

- Optional sparse OpenVDB solid union, ported from the upstream DynaMesh solver.
- Automatic recovery to the built-in C++ solver and independent Python node.
- A separately loaded OpenVDB library. The Maya plugin has no OpenVDB link dependency.
- Positive Resolution values with a soft 16–512 slider range. OpenVDB has no fixed modeling ceiling.
- Legacy backend limits remain explicit. Recovery never silently lowers Resolution.
- Runtime diagnostics, immutable sidecar installation, ownership protection, and scene reopening without source imports.
- Native inventory and an offline standalone Linux validation gate.

This prototype is local. No public push or release has been made.

## 0.1.0 — Initial standalone version

- Live `btDynaMesh` generator with C++ and independent Python evaluation.
- Optional CUDA distance batches with CPU recovery.
- Cached volume and surface fitting for parameter edits.
- Automatic temporary border closure and all-quad volume union.
- Maya module installer and selection-based Python API.
- Undo, scene reopening, baking, solver and deployment checks.

CUDA hardware validation is pending. The README links the YouTube demo. A GIF is optional.

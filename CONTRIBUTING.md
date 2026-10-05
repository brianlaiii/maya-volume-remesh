# Contributing

Use small pull requests with a clear problem, final behavior and test result.
Keep node registration, attributes and saved-scene filenames compatible.
Do not add a tool window. Parameters belong on the Maya node.

Run the host checks:

```bash
python -m unittest discover -s tests -p 'test_dynamesh_solver.py' -v
python -m unittest discover -s tests -p 'test_standalone_package.py' -v
python -m unittest discover -s tests -p 'test_dual_backend_loader.py' -v
```

See [testing](docs/testing.md) for Maya and CUDA checks.
State the Maya version, operating system, backend, input size and observed result.
Use generated primitives for reproducible examples. Keep large scene files outside Git.
Share a video or screenshots when the issue concerns visible surface quality.

Source and documentation use English. Runtime diagnostics must stay short and useful.
Keep fallback automatic, and never replace a live runtime in the same Maya session.

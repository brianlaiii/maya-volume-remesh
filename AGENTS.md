# Agent instructions

- Use source code as the authority. Write source, documentation and records in English.
- Keep this project standalone. Do not add dependencies on private tool repositories.
- Retain `btDynaMesh`, its type ID, attribute schema and deployed plugin filenames for saved scenes.
- Keep the C++ and independent Python implementations compatible. CUDA is optional.
- Use automatic CUDA to C++ to Python fallback. No custom tool window or modal fallback dialog.
- Never force-unload or overwrite an active Maya node runtime. Changed live code requires restart.
- Keep Maya scenes, model copies, raw videos and large generated data outside this checkout.
- Keep compiled plugins out of Git. Use release assets if binaries are distributed later.
- Run focused solver and runtime tests after relevant changes. Report skipped Maya/GPU checks clearly.
- Do not claim CUDA hardware validation until tests run on an NVIDIA host.
- Update affected documentation and the changelog for shipped behavior changes.

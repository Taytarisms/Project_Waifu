# JamePeng Vulkan Wheel Pilot

This pilot builds a Windows x64, Python 3.13 Vulkan wheel from pinned
JamePeng and llama.cpp revisions. By default, the wheel and its SHA-256
checksum are retained only as a GitHub Actions artifact; the binary is not
committed to the Project Waifu source tree or published as a release.

## One-user test gate

1. Leave `pilot.enabled` set to `false` in `llama_wheels.json` so ordinary
   setup runs cannot attempt to download an unpublished wheel.
2. Run the workflow with **Publish prerelease** unchecked, or build locally
   using the same pinned revisions and CMake options.
3. Give the wheel and checksum only to the selected tester. Install it into
   the tester's existing `files/.venv` after normal setup has installed the
   app dependencies.
4. Collect the tester's Vulkan device output, a real GGUF offload test, and
   `setup_log.txt` before enabling the manifest or publishing anything.

## Run the pilot

1. Push this build to a pilot branch in `Taytarisms/Project_Waifu`.
2. Set `pilot.enabled` to `true`, then in GitHub Actions run **Build JamePeng
   Vulkan wheel (pilot)** with **Publish prerelease** checked. If GitHub does
   not expose a branch-only `workflow_dispatch`, run it from the default
   branch of a temporary testing fork and update the `repository` value in
   `llama_wheels.json` to match that fork.
3. Confirm that the workflow creates prerelease
   `llama-vulkan-0.3.49-test.1` with both the wheel and `.sha256` assets.
4. Give wider testers a Project Waifu build containing the same
   `llama_wheels.json`. On AMD systems, `setup.bat` will try this verified
   wheel before attempting a local Vulkan source build.

The test computer needs a working AMD Vulkan driver/runtime. It does not need
Visual Studio C++ Build Tools or the Vulkan SDK.

## Create another pilot build

Published pilot tags are intentionally not overwritten. For another build:

1. Change `release_tag` in `llama_wheels.json` to the next test number.
2. If updating JamePeng, change both `jamepeng_ref` and `llama_cpp_ref` to
   tested full commit hashes.
3. If the package version changes, update the expected wheel and checksum
   filenames.
4. Run the workflow again.

## Acceptance checks

- Install on a clean Windows machine with an AMD GPU and no compiler or SDK.
- Confirm `llama_cpp.llama_supports_gpu_offload()` reports `True`.
- Load a text GGUF and verify that GPU layers are offloaded.
- Exercise at least one fork-only multimodal handler, including its mmproj.
- Confirm CPU fallback still works when Vulkan is unavailable.
- Confirm a modified wheel or checksum is rejected by the installer.
- Save `setup_log.txt` from each tester with the GPU and driver details.

After acceptance, promote the exact tested wheel and checksum assets. Do not
rebuild the production binary from the same source revisions, because that
would produce a different artifact than the one the pilot actually tested.

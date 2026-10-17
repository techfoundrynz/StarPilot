# StarPilot device CI

`Compile StarPilot` builds AGNOS ARM64 device binaries on GitHub's
`ubuntu-24.04-arm` runners. It runs on pushes to `StarPilot` and `Dom`, pull
requests, and manual dispatches. No self-hosted runner, SSH credentials, or
repository secrets are needed.

## Run and download

1. Open **Actions > Compile StarPilot > Run workflow**.
2. Select the branch and compiler job count (default: 4).
3. Download `starpilot-device-<commit>` from the completed run's artifacts.

The artifact contains `starpilot-device.tar.gz`, `SHA256SUMS`, and
`source-commit.txt`. The tarball is a binary overlay: native runtime binaries,
panda firmware, and the `prebuilt` marker. It preserves executable permissions.
Verify with `sha256sum -c SHA256SUMS`, then extract into a Linux checkout of
the exact commit in `source-commit.txt`. PR artifacts use the tested merge commit.
The overlay requires that checkout's source, models, third-party libraries, and
matching AGNOS runtime; it is not a standalone installation image.

Builds do not push binaries back into branches or deploy to devices. A successful
compile and architecture check is not an on-device runtime or driving test.
The laptop build deliberately skips regenerating the QCOM-captured driver
monitoring model and warp artifacts; use the ones from the matching checkout.

## Build environment

The workflow uses `tools/laptop_device_build/Dockerfile`, extracts its sysroot
from `system/hardware/tici/agnos.json`, then runs `./build`. Docker layers and
the extracted sysroot are cached. The sysroot cache key includes the AGNOS
manifest, extraction script, and Dockerfile, so an AGNOS update invalidates it.
The build depends on the upstream container registry, Python dependencies,
and AGNOS image host being available.

Binary artifacts and diagnostic logs are retained for 14 days. Failed builds
upload logs too. Superseded builds for the same branch or PR are cancelled.
The workflow has read-only repository access.

`Compile Bluescreens` and `Publish TrueNAS Runner Image` are legacy, manual
workflows. They are not needed for the hosted device build.

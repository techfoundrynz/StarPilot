# StarPilot device CI

For the day-to-day feature, review, release, and recovery process, start with
the [development and publishing workflow](development-workflow.md).

`Compile StarPilot` builds AGNOS ARM64 device binaries on GitHub's
`ubuntu-24.04-arm` runners. It runs on pushes to `custom`, pull requests targeting
`custom`, and manual dispatches. No self-hosted runner, SSH credentials, or
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

Successful push and manual builds of this repository's `custom` branch publish
the tested source tree plus verified binaries to `StarPilot`. A separate job has
write access; pull-request and upstream-candidate builds never publish. Publication
checks the artifact checksum, source revision, complete binary allowlist, and
AArch64 headers.
It skips superseded source and uses an atomic, non-forced push. Each release is
a snapshot whose only parent is the previous install commit, even when `custom`
has been rebased. Deleted source files stay deleted; old binary commits are never
merged into the source branch. A `device-<full-source-SHA>` annotated tag records
the release and its source revision. The release commit and tag publish together.
Generated commits skip CI, and install branch pushes do not trigger this workflow.

The installer can therefore clone matching source and binaries after publication
finishes. For this fork, the GitHub `openpilot` redirect makes the installer value
`techfoundrynz/StarPilot` resolve to this repository's `StarPilot` branch.

Builds do not deploy to devices. A successful compile and architecture check is
not an on-device runtime or driving test.
The laptop build deliberately skips regenerating the QCOM-captured driver
monitoring model and warp artifacts; use the ones from the matching checkout.

## Build environment

The workflow uses `tools/laptop_device_build/Dockerfile`, extracts its sysroot
from `system/hardware/tici/agnos.json`, then runs `./build`. Docker layers and
the extracted sysroot are cached. The sysroot cache key includes the AGNOS
manifest, extraction script, and Dockerfile, so an AGNOS update invalidates it.
The build depends on the upstream container registry, Python dependencies,
and AGNOS image host being available.
CI also extracts Pillow and its libraries from that exact AGNOS image and checks
a transparent WebP round-trip on ARM64 before building, verifying the pet store's
decoder is present in the target runtime rather than only in the build container.

Binary artifacts and diagnostic logs are retained for 14 days. Failed builds
upload logs too. Superseded builds for the same branch or PR are cancelled.
The build job has read-only repository access; only the trusted publication job
can write its generated binary commit.

`build_device.yaml` is the shared read-only builder used by ordinary CI and the
manual upstream-update workflow. `publish_device.yaml` is the shared publisher.
See [Maintaining the fork](upstream-updates.md) for preparing, reviewing, and
accepting upstream updates without publishing unreviewed candidates.

`Compile Bluescreens` and `Publish TrueNAS Runner Image` are legacy, manual
workflows. They are not needed for the hosted device build.

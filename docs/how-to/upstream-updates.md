# Maintaining the StarPilot fork

This guide covers upstream synchronization. For feature PRs, release triggers,
publication checks, and recovery, see the
[development and publishing workflow](development-workflow.md).

## Branches

| Ref | Purpose |
| --- | --- |
| `upstream/StarPilot` | Local remote-tracking ref for `firestar5683/StarPilot` |
| `origin/upstream-base` | Exact upstream commit underlying the accepted custom patch stack |
| `custom` | Default development branch: upstream plus our focused source commits |
| `StarPilot` | Append-only installation history: source snapshots plus matching device binaries |
| `updates/upstream-<run>-<attempt>` | A proposed rebased source tree, awaiting review |

The install address remains `techfoundrynz/StarPilot`. Never develop on, rebase,
or force-push the install branch. Only successful builds of accepted source
publish to it. The source branch still contains files tracked by upstream,
including upstream binaries; our generated publication commits are kept off it.

## One-time local setup

```sh
git remote add upstream https://github.com/firestar5683/StarPilot.git
git config rerere.enabled true
git config rerere.autoupdate false
git fetch origin
git fetch upstream
git switch --track origin/custom
```

Skip `remote add` or `switch --track` if that remote or branch already exists.
`rerere` remembers local conflict resolutions. Review reused resolutions before
staging them. Fresh GitHub runners do not share your local resolution cache.

## Prepare and accept an upstream update

1. In **Actions > Update upstream > Run workflow**, select **custom** and leave
   **accept_run** empty.
2. The workflow fetches upstream and rebases only the commits after
   `upstream-base` onto the new upstream revision. Neither accepted source nor
   the install branch changes. If upstream is unchanged, it exits successfully
   without a build. If conflicts occur, it stops and saves a conflict report.
3. For a clean rebase, inspect the candidate branch and `upstream-candidate`
   artifact. `review.txt` contains a `git range-diff` of our old and new commits.
   Review upstream's changes as well. The workflow explicitly invokes the shared
   device builder, including CI regression tests; it does not rely on a bot push
   triggering another workflow.
4. Once the **whole preparation run succeeds** and the changes are reviewed, run
   **Update upstream** again on **custom**, putting that preparation run's numeric
   ID in **accept_run**. This is the explicit acceptance step.
5. Acceptance verifies the successful run, unchanged candidate, and unchanged
   source/baseline. It updates `custom` and `upstream-base` atomically with exact
   leases, retaining the old source at `source-backup/<run>`. It then publishes
   the already-tested binaries as a new `StarPilot` snapshot. No second build is
   needed. Artifacts expire after 14 days; prepare again if they have expired.

If source changes while a candidate is under review, prepare again. Never merge
the rebased candidate through an ordinary GitHub PR merge, which would reintroduce
the old patch history. A failed publication leaves the previous install intact;
rerun the acceptance workflow to retry while the accepted source is still current.

## Resolve a conflict locally

Use a clean checkout and keep unrelated work on a separate branch first:

```sh
git fetch origin
git fetch upstream StarPilot
git switch -c updates/local-upstream origin/custom
git rebase --onto upstream/StarPilot origin/upstream-base
# Resolve the reported conflicts, git add the resolved files, then:
git rebase --continue
```

Run the relevant tests and review `git range-diff` before promotion. Capture the
original `origin/custom` and `origin/upstream-base` SHAs before starting. Promotion
must atomically push the candidate to `custom` and the chosen upstream revision
to `upstream-base`, with explicit `--force-with-lease=<ref>:<original-SHA>` guards
for both refs, and a backup tag for the original source. Do not use a blanket
`git push --force`. A human push to `custom` triggers CI; the install branch only
advances after that build succeeds. If either original ref changed, start again.

## Work on our features

Branch from `custom`; target feature PRs at `custom`. Squash development fixups
into their feature before merging. Keep CI, pets, and vehicle changes separate,
and use small integration changes to call custom modules. Avoid unrelated
formatting and generated binaries in feature commits. Submit generally useful
changes upstream where appropriate so they can eventually leave our patch stack.

After an accepted rebase, local branches may diverge. Fetch first and preserve
local work; replay unpublished commits onto the new `origin/custom`. Do not
`git pull` a merge of the old and new source stacks. Check out a fresh branch at
`origin/custom` if the old local branch contains no work you need.

The source split preserved its original install tip at `migration/pre-source-split`.
The later install-history cleanup retained the full pre-cleanup history at
`migration/pre-install-history-cleanup`, then established one initial release
above upstream with identical files and binaries. Both backups are tags; normal
install publication is append-only.

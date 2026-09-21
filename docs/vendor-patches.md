# Vendoring llama.cpp forks as patch series

Telecode builds llama.cpp from source with a patch series on top
(`llamacpp/patcher.py`). Two kinds of patch live there, and they are not the
same kind of thing:

```
patches/llama.cpp/
  0001-common-add-defer_loading-to-tool-definitions.patch   <- ours
  prism/                                                    <- a vendored fork
    0001-..0010-*.patch
    VENDOR.json
```

**Top-level patches are telecode's own** — small, single-purpose, written to be
proposed upstream. `git apply --check` failing on one is a *result*: it landed
upstream, or it bit-rotted. That is the shape `patcher.py` was built for.

**A subdirectory is a whole third-party fork, vendored.** It exists because some
model weights simply cannot be read by stock llama.cpp — a fork defines its own
ggml types and kernels, and the GGUF on Hugging Face is in that format. There is
no small patch that buys this; it is thousands of lines or nothing.

Vendoring one is a standing cost, taken deliberately. This document is how to
keep that cost bounded.

## Why we carry the port ourselves

The tempting arrangement is to track the vendor's branch and regenerate whenever
they rebase onto newer upstream. It is cheaper — their rebase carries their own
testing, which ours never will.

It is also a dependency on someone else's release cadence, and it fails in the
way dependencies fail: silently, at the worst time. PrismML rebases roughly
every 4–6 weeks in large jumps, and their release tags (`prism-b10709`) encode
*their own commit count*, not the upstream base — the base had been b10615 for
over three weeks while the tag numbers kept climbing. A vendor that stops
publishing, or slows down, freezes us at whatever llama.cpp they last targeted.

So: **we own the merge.** `tools/vendor_drift.py` exists to make owning it
survivable, and to make regenerating from the vendor cheap *when* they do rebase,
so we can take that discount whenever it is offered without depending on it.

## VENDOR.json

Every vendored directory carries one. It is what the tooling reads.

```json
{
  "name": "prism",
  "repo": "https://github.com/PrismML-Eng/llama.cpp.git",
  "branch": "prism",
  "vendor_commit": "9a9394a...",
  "vendor_base":   "5ea87dd...",
  "ported_onto":   "b11065",
  "generated":     "2026-09-21",
  "claims": {
    "ggml_types": { "PQ2_0": 142, "PTQ1_0": 143 },
    "file_types": { "MOSTLY_PQ2_0": 128, "MOSTLY_PTQ1_0": 129 },
    "archs": ["dspark", "dflash", "dfly"]
  }
}
```

| field | meaning |
| --- | --- |
| `vendor_commit` | the fork commit this series was ported **from** |
| `vendor_base` | the upstream commit the fork itself branched from — how we detect their rebase |
| `ported_onto` | the upstream tag this series applies to cleanly |
| `claims` | ggml type ids, file-type ids and arch strings this vendor takes |

`claims` is not documentation. Two vendored forks that both take ggml type id
142, or both register the arch string `dspark`, **compile fine and then silently
misread weights** — there is no link error, no exception, just wrong numbers.
`vendor_drift.py collisions` is the only thing that catches it, so a new vendor's
claims must be filled in truthfully or the check is theatre.

## The tool

```bash
python tools/vendor_drift.py status               # integrity + drift + catch-up
python tools/vendor_drift.py conflicts prism --onto b11200 --out report.md
python tools/vendor_drift.py collisions
```

It manages its own blobless clone under `data/vendor-src/` and never touches the
real build checkout.

### `status`

Four questions, because four different things can be wrong and they need
different responses:

1. **Does the series apply to `ported_onto`?** If not, someone hand-edited a
   patch or `VENDOR.json` is stale. Nothing else in the report is trustworthy
   until this is clean.
2. **Does it apply to the newest upstream tag?** This is the drift. It also
   retries with `--3way` and reports that number separately — the gap between
   the two is the difference between "upstream moved nearby" and "upstream moved
   the code we patch".
3. **Has the vendor added commits since `vendor_commit`?** Work we have not
   ported — bug fixes in kernels we are shipping.
4. **Has the vendor rebased?** If `vendor_base` moved, regenerating from their
   new base is cheaper and safer than carrying our own merge forward. Take it.

### `conflicts`

The one that matters for staying current. It 3-way-applies the series onto a
target ref and prints **only the hunks git could not resolve**, with context,
grouped by file — the hunks git *did* resolve are not shown, because they are
not decisions anyone needs to make.

The output is written to be handed to a model along with the repo. It states the
resolution rule at the top, and which side is which (`ours` = the vendor's code,
`theirs` = upstream), because that mapping is inverted from what most people
assume when reading a merge they did not start.

### `orphans`

The failure mode with **no error message**, and the reason this subcommand
exists: when upstream moves a struct into a brand-new header, that header is a
pure addition. It conflicts with nothing, the series still applies cleanly, and
the vendor's additions to the moved struct are simply gone.

This is not hypothetical — it happened twice in the prism port onto b11065:

- upstream moved `vk_device_struct` into a new `ggml-vulkan-types.h`, and the
  fork's FWHT pipeline members vanished with it. The file would not have
  compiled, and nothing in the merge said so.
- upstream hoisted Metal dispatch predicates into `ggml-metal-common.cpp`,
  including its own narrower `ggml_metal_fwht_supported_size()` (64–512). The
  fork's copy in `ggml-metal-device.h` said 64–8192. Two statics in two
  translation units, no redefinition error, silently disagreeing about which
  widths `supports_op` admits versus which the dispatch accepts.

A file that did not exist when the vendor wrote their change cannot be diffed
against it, so there is nothing to detect mechanically. `orphans` narrows where
to look instead: upstream files added between `ported_onto` and the target, in
directories the series already edits. A hint, not a proof — **the only real
check is compiling.**

## Resolution rule

When resolving, the target is **upstream-latest plus the vendor's feature**.

- Never take one side wholesale. Taking the vendor's side reverts upstream work
  and produces a patch that *deletes* code — worse than useless. Taking
  upstream's side deletes the feature the vendor exists for.
- **Check first whether upstream has since implemented the feature itself.**
  This is the single highest-value check and it came up repeatedly in the prism
  port: the fork's hand-rolled DGX-Spark L2 prefetch in `ggml-cuda/mmvq.cu` now
  exists upstream as `mmvq_should_prefetch()` / `mmvq_prefetch_l2()`, and its
  HIP crumb-unpack helper in `vecdotq.cuh` now exists upstream inline. In both
  cases the right answer is to keep **upstream's** version and extend it to the
  vendor's extra types — not to keep a parallel copy that will drift.
- Where upstream renamed or re-signatured something the fork calls, update the
  fork's call. (Example found in the port: upstream split
  `init_mul_mat_id_tensors(ctx, n)` into `init_mul_mat_id_ids(ctx, n)` plus a new
  `init_mul_mat_id_tensors(ctx, n, amax)`.)
- If you cannot tell, leave `TODO(<vendor>-merge): <question>` rather than
  guessing. A wrong resolution in a quant kernel compiles and returns garbage.

## Regenerating a series

Two routes. Prefer the first.

**The vendor rebased** (`status` says so) — regenerate from their new base:

```bash
# in data/vendor-src/llama.cpp
BASE=$(git merge-base upstream/master prism/prism)
git describe --tags "$BASE"                    # -> the new ported_onto tag
git diff --binary --no-renames "$BASE"..prism/prism > prism-full.diff
python tools/split_prism.py prism-full.diff patches/llama.cpp/prism
# then update vendor_commit / vendor_base / ported_onto in VENDOR.json
```

**We are moving ahead of the vendor** — merge upstream into their branch
ourselves, resolve, and regenerate from the merge:

```bash
git worktree add ../rebase prism/prism --detach
cd ../rebase && git merge <new-tag>
# ... resolve (see the rule above; use `vendor_drift.py conflicts` for the report)
git add -A && git commit -m "merge <new-tag> into prism"
git diff --binary --no-renames <new-tag>..HEAD > prism-full.diff
python tools/split_prism.py prism-full.diff patches/llama.cpp/prism
```

`split_prism.py` refuses to write if any changed file matches none of its file
groups, so a new top-level directory in the fork surfaces as a hard failure
rather than silently vanishing from the series.

## Verifying

Per-patch `--check` passing does **not** prove the result is right. Reconstruct
and compare:

```bash
git worktree add ../verify <ported_onto>
cd ../verify && for p in patches/llama.cpp/prism/*.patch; do git apply "$p"; done
git add -A -f && git write-tree     # compare against the merge commit's tree
```

And then build. A clean apply says nothing about whether it compiles, and
compiling says nothing about whether a quant kernel returns correct numbers —
that needs the actual model. State which of the three you have actually done.

## The standing trap

Unchanged from `patcher.py`'s docstring, and worse with a vendor in the series:
**Version Manager → "Update Now" overlays a release zip onto the same install
directory and silently replaces a patched build.** With a vendored fork that
means losing the custom quant types, and the failure presents as a corrupt or
unreadable GGUF rather than as a missing patch. `status()`'s `build_is_installed`
is what reports it.

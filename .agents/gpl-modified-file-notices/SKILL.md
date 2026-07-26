---
name: gpl-modified-file-notices
description: >-
  MANDATORY before committing any change in this repo: this project is a
  GPL-3.0 fork of r4nd3l/agent-session-manager, and GPLv3 §5(a) requires every
  modified file to carry a prominent notice that it was changed, with a date.
  Use whenever you edit, refactor, rewrite, or delete ANY file that existed
  before the fork point (commit a3a5a77) — which is almost every existing file
  in this repo — to add or refresh the required modification-notice comment.
  Not needed for files newly created in this fork.
---

# GPL modification notices for pre-fork files

This repo is a fork of
[r4nd3l/agent-session-manager](https://github.com/r4nd3l/agent-session-manager),
licensed GPL-3.0. We intend to release/distribute this fork, so GPLv3 §5(a)
applies: *"The work must carry prominent notices stating that you modified it,
and giving a relevant date."* The conservative, conventional way to satisfy
this is a per-file notice in every file we change from the upstream original.

**The rule: any commit that changes a pre-fork file must leave that file
carrying an up-to-date modification notice.**

## Does the file need a notice?

A file needs a notice if **both** are true:

1. You are changing its contents (any edit counts, including trivial ones —
   formatting, typo fixes, deletions of lines).
2. It existed at the fork point, commit `a3a5a77` (the last upstream commit,
   2026-06-21). Check with:

   ```bash
   git cat-file -e a3a5a77:path/to/file 2>/dev/null && echo pre-fork || echo fork-new
   ```

Files created in this fork (`fork-new`) do **not** need a modification notice.
If we ever merge new commits from upstream, the fork-point hash above must be
revisited — update this skill in the same PR as the merge.

## The notice

Exactly one notice per file, placed at the top — after any shebang or
XML/encoding declaration, and before or alongside any existing header
comments. Two lines:

```
Modified from the original agent-session-manager
(https://github.com/r4nd3l/agent-session-manager, GPL-3.0) in the ghackett
fork. Last modified: YYYY-MM-DD. Full change history: git log for this file.
```

wrapped in the file's comment syntax, e.g.:

- Python, shell, PKGBUILD, `.desktop`, `.po`, YAML, TOML (`#`):

  ```python
  # Modified from the original agent-session-manager
  # (https://github.com/r4nd3l/agent-session-manager, GPL-3.0) in the ghackett
  # fork. Last modified: 2026-07-26. Full change history: git log for this file.
  ```

- Markdown, HTML, XML, `.ui`, `.svg`: the same text in a `<!-- ... -->` block.
- CSS / JavaScript: the same text in a `/* ... */` block.

Rules:

- **Adding**: if the file has no notice yet, add one with today's date.
- **Updating**: if the file already has a notice, update its `Last modified:`
  date to today. Never stack a second notice.
- **Deleting a pre-fork file**, or modifying a pre-fork file whose format
  cannot carry comments (JSON, images, other binary assets): record it instead
  as a line in the top-level `NOTICE` file (create the file if it doesn't
  exist yet) in the form
  `- <path>: modified|deleted in this fork, YYYY-MM-DD`.

## Before committing

Scan your staged diff: for every changed file, confirm it is either fork-new,
or carries a notice dated today (or a `NOTICE` entry, for the comment-less
cases). Do this in the same commit as the change itself — a pre-fork file must
never land modified-but-unnoticed, even in intermediate commits.

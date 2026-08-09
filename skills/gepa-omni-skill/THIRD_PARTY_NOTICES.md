# Third-party notices

## GEPA native-runtime provenance

Portions of the plugin-native runtime in:

- `scripts/native_omni/core.py`
- `scripts/native_omni/runners.py`

are adapted from the MIT-licensed GEPA project at the pinned upstream commit
[`8a2bed96385202f69caaeb5327a843ed2f5ea225`](https://github.com/gepa-ai/gepa/tree/8a2bed96385202f69caaeb5327a843ed2f5ea225).
The source headers in those files repeat this provenance. Adaptation is limited
to the task/evaluation primitives and runner/budget contracts; the
plugin-native implementation is deliberately independent of an installed GEPA
checkout and uses its own OpenAI-compatible Chat Completions client.

The applicable MIT license text is shipped as the repository/plugin-root
[`LICENSE`](../../LICENSE). In summary, permission is granted to use, copy,
modify, merge, publish, distribute, sublicense, and sell copies of the covered
software, subject to retaining the copyright and permission notice. The
software is provided without warranty.

This notice is part of the installable `skills/` payload and must remain in
portable and Codex staging outputs.

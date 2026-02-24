# Contributing to AgentMesh

## Developer Certificate of Origin (DCO)

By contributing to this repository, you certify that your contribution complies with the [Developer Certificate of Origin (DCO)](https://developercertificate.org/), version 1.1.

Sign every commit with:

```bash
git commit -s
```

This adds a `Signed-off-by` trailer to your commit.

## Contribution License

Unless you explicitly state otherwise, any contribution intentionally submitted for inclusion in this project is licensed under the repository license (Apache-2.0).

## Pull Request Checklist

- Include focused tests for behavior changes.
- Keep migrations idempotent and backward-safe.
- Preserve fail-closed semantics for coordination/provenance flows.
- Update docs when CLI or policy behavior changes.

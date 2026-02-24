## Summary

- What changed:
- Why:

## Public/Private Review (required)

- [ ] I ran `agentmesh classify --staged --fail-on-private --fail-on-review` and it passed.
- [ ] I ran `agentmesh release-check --staged --json` and it passed.
- [ ] No raw private run artifacts are included (CI logs, raw canary reports, internal strategy docs).
- [ ] If any docs were added/updated, they are sanitized for OSS publication.

## Validation

- [ ] Tests updated/added where needed.
- [ ] Local test run completed (`uv run pytest -q` or targeted equivalent).

## Notes

- Risks / follow-ups:

<!-- Sprint S2-EXT-5 — Peer-review PR template. Every frontend PR
     touching `apps/web/src/auth/`, `apps/web/src/observability/`, or
     `src/application/headless/oidc_principal_resolver.py` MUST have at
     least one human approval before merge. Peer review surfaces
     defect categories no automated tool reaches (cognitive load,
     API ergonomics, naming, domain-knowledge gaps).

     CODEOWNERS routes auth-related PRs to the on-call reviewer
     automatically. -->

## Summary

<!-- 1-3 bullet points on what changed and why. -->

## Self-audit (cumulative discovery pattern — S2-α → S2-EXT)

- [ ] I walked `.claude/checklists/sprint-self-audit.md` and recorded
      defects under `## 자평 audit` in this sprint's evaluation file.
- [ ] If this PR touches `apps/web/src/auth/`, `apps/web/src/observability/`,
      or `src/application/headless/oidc_principal_resolver.py`, a peer
      review (CODEOWNERS-routed) is requested before merge.

## External-tool gates (Sprint S2-EXT chain)

- [ ] axe-core (`npm run test:e2e -- a11y.spec.ts`) — zero critical/serious.
- [ ] Semgrep (`semgrep --config p/typescript --config .semgrep.yml --severity ERROR apps/web/src/`) — zero ERROR.
- [ ] OIDC conformance (with Keycloak compose + `E2E_OIDC=1`) — discovery + JWKS shape passes.
- [ ] OWASP ZAP (when scope warrants — see roadmap) — zero High.

## Test plan

<!-- Bulleted checklist of TODOs for testing this PR. -->

🤖 Generated with [Claude Code](https://claude.com/claude-code)

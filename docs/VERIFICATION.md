# Phase 1 verification evidence

Verified 2026-10-27 against foundation commit
`f26e5e725c34dfa4e568476dd6e8853807d5bb99`.

Repository: https://github.com/Saurx9611/chronos-distributed-scheduler

Hosted run: https://github.com/Saurx9611/chronos-distributed-scheduler/actions/runs/40476193112

All three jobs (`python`, `frontend`, `containers`) and their individual check steps
returned success. This is foundation verification, not queue-engine certification.

| Check | Local Windows | Hosted Ubuntu |
| --- | --- | --- |
| Python dependency lock | uv lock --check --offline passed | uv sync --locked passed |
| Ruff lint / format | Passed | Passed |
| Strict mypy | Passed, 4 source files | Passed |
| pytest | 2 passed, 1 skipped (no DB engine) | Passed with PostgreSQL 18 configured |
| pip-audit --skip-editable | No known vulnerabilities; local editable project excluded | Passed |
| Alembic heads | Passed; no released revisions | Passed |
| npm ci | Passed clean install | Passed |
| ESLint / TypeScript | Passed | Passed |
| Next.js production build | Passed | Passed |
| npm audit | No known vulnerabilities | Passed high-severity gate |
| Compose config | Passed | Passed |
| API/frontend image builds | Not run: Docker engine stopped | Passed |
| Public repository clone | Passed into ignored .tools/clone-check | checkout/install passed |

Local versions: Git 2.55.0.windows.5, Python 3.14.7, uv 0.13.6, Node 24.21.0,
npm 11.19.0. Key locked packages: FastAPI 0.162.0, Pydantic 2.14.1,
SQLAlchemy 2.1.6, Alembic 1.20.1, psycopg 3.3.6, Next.js 16.4.3,
React 19.3.1, TypeScript 5.9.3, Tailwind 4.3.3 and ESLint 10.13.0.

The Next.js aggregate lint preset still pulled plugins with incompatible ESLint 10
peer ranges. The final configuration uses compatible current ESLint core,
typescript-eslint, the official Next plugin and React Hooks rules directly. A clean
install and hosted checks confirmed the resulting dependency graph.

Initial sandbox-only pytest runs stalled and Turbopack could not start subprocesses;
authorized reruns outside the sandbox passed. This did not require changing app
behavior. PostgreSQL DDL validates constraints and submission-key uniqueness, not
distributed claim/lease behavior. No load or performance test was run.

Use the exact commands in README.md to reproduce. Full hosted command evidence is
in the linked run. Advisory scan results are time-sensitive, not a security guarantee.

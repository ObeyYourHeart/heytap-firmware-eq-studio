# HeyTap Firmware EQ Studio — Claude Code project instructions

## Project baseline
- This repository's meaningful application code is on `feat/python-desktop-studio` at baseline `e62d5d161d4436146ec9cd71b653c7050ba32214`, **not** the upstream default `main`.
- Read `HANDOFF_FOR_CLAUDE_CODE_2026-10-11.md` and `docs/unified-workspace.md` before changing product behavior. Review the actual code and current CI; historical docs contain obsolete statements.
- Goal: locally packaged Python/PySide6 desktop application with an embedded DSSSP/React Qt WebEngine graph, first firmware adapter OPPO Enco X4. Do not invent a remote backend or add mandatory runtime Node/Electron.

## Invariants
- Firmware parsing, DSP, measurement fitting, session, and writing logic remain in Python modules (`src/heytap_eq/`), isolated from Qt where feasible. Graph UI is `frontend/`, bridged with Qt WebChannel in `src/heytap_eq/web_plot.py`.
- Do not hardcode EQ index 45 or Enco X4 offsets as universal behavior. Index 45 is a special Enco X4 adapter configuration. `DAC1`/`DAC2` are display aliases, not conclusively physical DAC identities. Both outputs and both other/india regions require preservation.
- Acoustic measurement, digital transfer functions, and predicted acoustic response are different. Current dual-output prediction contains an explicit power-summation assumption. Never claim that predictions are on-device measurements.
- Do not overwrite original firmware or write unknown layouts without structural/semantic verification. Preserve input-hash association, validation/repack, output report, state/region fidelity, stability and 44.1/48/96 kHz checks.
- Project v4 (with backward compatibility), undo/redo, complete tuning/preset clipboard, cancellation/stale-result rejection, and existing native packagers must remain functional.
- Never echo or commit tokens/cookies/raw HAR/whole APK/proprietary firmware or expose existing `service_profile.py` credentials in output/logs. Existing public bundled authorization needs explicit maintainer discussion before removal/rotation.
- Do not update upstream branches or merge upstream PR #1 without explicit maintainer instruction. Develop as small reviewable commits in the fork.

## First tasks
1. Confirm branch/SHA and run baseline tests; capture real Qt app screenshot before UI changes.
2. Fix fork-compatible GitHub Actions triggers and add deterministic frontend dependency lock; keep Windows PyInstaller/Nuitka real executable smoke and comparison checks.
3. Propose UX changes emphasizing the large unified response chart and the sequence Measurement → Target → Correction → Preset fit → Export; keep advanced config discoverable but collapsed.
4. Preserve backend and WebChannel contract; add regression and browser/Qt interaction checks with each UX refactor.

## Development

```bash
cd frontend && npm install --no-audit --no-fund && npm run build && npm test
# playwright once installed: npm run test:browser
cd ..
python -m pip install -r requirements-ci.txt
python -m pip install --no-deps --no-build-isolation -e .
python -m ruff check .
python -m pytest
python -m heytap_eq.app
```

Requires Python 3.11, Node 22 and compatible Qt graphics environment. Windows portable builds should be validated by `.github/workflows/desktop.yml`. Baseline 2026-10-11 CI run: https://github.com/uselessbug/heytap-firmware-eq-studio/actions/runs/38104329655 (53 Python tests passed on each Linux and Windows runner, both packagers passed).

For each delivered PR include: user-visible changes, before/after **real desktop** screenshots at small and regular resolutions, test/CI evidence, performance evidence where relevant, risks and remaining unknowns.

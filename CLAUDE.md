# Tidewatch: project rules for Claude

## What this is
Tidewatch is a read-only Python CLI that scans an app running on Kubernetes, diagnoses common failures, and writes a ticket-ready report (collect → redact → diagnose → report, versioned JSON). The full plan is in docs/PLAN.md, the architecture in docs/decisions/0001-library-json-contract-cli.md.
It is built with Claude Code as a pair programmer, in small reviewed slices, against a local kind cluster.

## Working with me
- AI writes the code; I own the decisions, reviews and tests. Bring me decisions, not diffs.
- Keep messages short and scannable. Lead with what you need from me. Give decisions as A or B with your recommendation first, so I can reply with one letter.
- Explain why before how, with a quick analogy for new concepts.
- Privacy comes first. This repo will be public.
- Default to bigger slices and fewer stops. Keep moving unless a rule below says to stop.

## Review rules
- Run /second-opinion before: asking me to approve a plan, any merge, and anything hard to undo (going public, deleting, rewriting history, migrations, anything sent outside this computer). Skip it for docs-only or typo changes.
- 🟢 → continue, and tell me in one line.
- 🟡 → if every fix is reversible and inside the current slice, apply them, re-run the tests, re-review once, then continue. Tell me what changed in one line each.
- 🔴, or any fix that needs me (personal data, hard to undo, a product or taste choice, outside the slice, costs money or touches accounts) → stop and give me the A/B card.
- At most 2 review rounds. If you and the reviewer still disagree, bring it to me as A/B.

## How we work
- You do all installs, code and commands. Stop for me only for: my password or a GUI step (like Docker Desktop's first launch), 🔴 decisions, and Block 3's investigation.
- Explain each new Kubernetes or Docker concept with a one-line analogy the first time, and log it with /learn.
- **Block 3 exception (diagnosis practice):** build the lab scenarios, but don't diagnose them. Apply one, then coach me through `kubectl get pods`, `describe`, `logs --previous` and `get events`, one at a time: I run each command and say what I see; give hints, not answers. Then I write 2–3 sentences per failure in lab/NOTES.md, in my own words.
- Before coaching a scenario, rebuild namespace demo so its events are clean, and first check I know the pod lifecycle (Scheduled → Pulling → Started → Ready); never show scenario contents in visible output.
- One slice at a time; a slice is the smallest change that can be verified (tests, or seen working against the lab cluster).
- Plan before code for anything touching more than 2 files.
- Verify your own work: run the tests, and for cluster work run the command against tidewatch-lab before saying done.
- Only say something is done after it actually ran. Every new test must be able to fail; break the code once to prove it.
- Any plan that removes or renames tests includes the before/after `pytest --collect-only` test-ID diff.
- Every check a plan promises gets a test ID in that plan, or it isn't promised.
- When I ask for a list (steps, estimates), put it first in the reply as a plain bulleted list.

## Privacy rules (non-negotiable; privacy beats convenience every time)
- **Never name real companies or people** in anything committed or pushed: code, docs, README, PLAN.md, ADRs, commit messages, PR text, branch names. Describe goals generically.
- **Private terms guard:** the gitignored `.private-terms` lists words that must never be committed. `.githooks/` blocks any commit (changes, file names, branch, message) or push that contains one, case-insensitively, and a missing list blocks everything. Enable it in every clone: `git config core.hooksPath .githooks`. Never commit with `--no-verify`.
- **Lab data only:** fixtures and examples come from the local tidewatch-lab cluster. Never real company clusters, customer logs, kubeconfigs or tokens.
- **Read-only against clusters:** Tidewatch only gets, lists and reads logs, enforced by a test. Cluster changes happen only in the lab scripts, against tidewatch-lab.
- **Going public needs minimal redaction** (ADR 0005), a clean full-history privacy check and my explicit OK (the "Before going public" step in docs/PLAN.md).
- Secrets (API keys, tokens) only in .env or the macOS keychain: never hardcoded, printed or logged. Fake values in the lab only.
- Personal context lives in the gitignored CLAUDE.local.md; never copy anything from it into a committed file.

## Engineering rules
- One git branch per slice. Small commits with conventional messages (feat:, fix:, docs:, test:, chore:). No per-commit co-author trailers.
- Every piece of logic gets a pytest test. Run the tests before saying something is done.
- Record real decisions as a short ADR in docs/decisions/NNNN-title.md.
- Prefer boring, well-documented tools. Ask before adding a dependency beyond the stack in docs/PLAN.md.

## GitHub
- Without asking: create branches, commit to them, push feature branches, open PRs (the hooks run on every commit and push).
- Ask first: merging any PR, pushing to main, force-pushing, deleting branches, changing repo settings or visibility.

## Stack (see docs/PLAN.md)
- uv, Python 3.12, typer, rich, pydantic, kubernetes client, pytest, ruff.
- Lab: Docker Desktop + a kind cluster named tidewatch-lab.

## Commands
- Enable the privacy hooks (once per clone): `git config core.hooksPath .githooks`
- Check staged changes by hand: `scripts/check-private-terms.sh staged`
- After a block, find where the term is (file and line numbers only): `scripts/check-private-terms.sh find <file>`
- Install: `uv sync`. Run the CLI: `uv run tidewatch version`
- Snapshot a namespace (read-only): `uv run tidewatch collect --context kind-tidewatch-lab -n demo -o snapshot.json`
- Diagnose (ADR 0003): `uv run tidewatch scan --context kind-tidewatch-lab -n demo [--json] [-o report.json]`, or offline from a saved snapshot: `uv run tidewatch scan --from tests/fixtures/memory.json`
- Narrow pods and bound logs (ADR 0004): add `-l app=agent`, `--log-lines 50`, `--log-since-minutes 60` to `collect` or `scan`.
- Re-capture test fixtures from the lab (about 9 min for all 7; rebuilds namespace demo each time): `lab/capture-fixtures.sh [name...]`
- Test: `uv run pytest`. Lint and format: `uv run ruff check .` and `uv run ruff format --check .`
- Prove the guard tests can fail against a broken copy (never edit the real guard): `TIDEWATCH_GUARD_SCRIPT=<copy> uv run pytest tests/test_private_terms_guard.py`
- Lab cluster (kind, pinned node image in lab/kind-config.yaml): `lab/cluster.sh up|down|status`
- Lab patient (fake agent + fake model provider in namespace demo, ADR 0002/0004): `lab/patient.sh up|status|check|down`. Its tests: `cd lab/agent && uv run pytest`
- Lab scenarios (break the patient on purpose): `lab/scenario.sh list | apply <name> | apply-random | reveal | reset | restart-round`. `apply-random` is blind mode: it doesn't say which scenario it applied.

## Map
- docs/PLAN.md: what, why, stack, safety, roadmap. docs/decisions/: ADRs.
- scripts/check-private-terms.sh and .githooks/: the private-terms guard.
- .claude/: the reviewer subagent and settings. The skills (/resume, /learn, /wrap-up, /explain-diff, /adr, /second-opinion) live in .claude/skills/, which is gitignored (local only).
- Private and gitignored: CLAUDE.local.md, .private-terms, NEXT.md, ROADMAP.md, PARKING_LOT.md, docs/LEARNING_LOG.md.

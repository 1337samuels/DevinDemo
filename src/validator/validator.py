"""Part 2 --- Validate that identified items are truly legacy / dead code.

Uses a **single Devin session** with sequential prompts via ``send_message()``:

1. **Setup** --- create a session with a neutral prompt and wait for it to
   become ready.
2. **Batch validation prompts** --- ``send_message()`` for each batch of
   related candidates, running 8 validation layers per candidate.

The session is sent to sleep after all batches complete.

``send_message()`` uses the **v1** API endpoint which works with ``cog_``
service-user keys without requiring ``ManageOrgSessions``.

The 8 validation layers are:

1. Re-Confirm the Detection
2. Git History Staleness
3. Active Development Cross-Reference
4. Static Reachability Analysis
5. Issue & Discussion Archaeology
6. Test Coverage & Test References
7. Runtime & Deployment Signals (best-effort)
8. Cross-Repository & External Consumers

Each batch returns structured results per layer plus an overall
confidence level (``EXEMPT``, ``HIGH``, ``MEDIUM``, ``LOW``).
"""

from __future__ import annotations

import json
import re
import time
from collections import defaultdict
from typing import Any, Callable

from src.api.client import DevinAPIClient

# ---------------------------------------------------------------------------
# Confidence levels (ordered from most to least confident the code is dead)
# ---------------------------------------------------------------------------

CONFIDENCE_EXEMPT = "EXEMPT"
CONFIDENCE_HIGH = "HIGH"
CONFIDENCE_MEDIUM = "MEDIUM"
CONFIDENCE_LOW = "LOW"

# ---------------------------------------------------------------------------
# Structured output schema for a single validation session
# ---------------------------------------------------------------------------
# Each session validates one batch of related candidates and returns
# per-candidate layer results plus an overall verdict.

VALIDATION_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "candidates": {
            "type": "array",
            "description": "Validation results for each candidate in this batch.",
            "items": {
                "type": "object",
                "properties": {
                    "candidate_id": {
                        "type": "string",
                        "description": "The finding ID from Part 1 output.",
                    },
                    "layer_results": {
                        "type": "object",
                        "description": "Results from each of the 8 validation layers.",
                        "properties": {
                            "layer_1_reconfirm": {
                                "type": "object",
                                "properties": {
                                    "confirmed": {
                                        "type": "boolean",
                                        "description": (
                                            "True if the detection is confirmed as real "
                                            "code (not a comment/string/doc)."
                                        ),
                                    },
                                    "method": {
                                        "type": "string",
                                        "description": "How the verification was performed.",
                                    },
                                    "explanation": {
                                        "type": "string",
                                        "description": "Details on what was found.",
                                    },
                                    "additional_files": {
                                        "type": "array",
                                        "items": {"type": "string"},
                                        "description": (
                                            "Other files that also reference this candidate."
                                        ),
                                    },
                                },
                                "required": ["confirmed", "method", "explanation"],
                            },
                            "layer_2_git_staleness": {
                                "type": "object",
                                "properties": {
                                    "last_meaningful_edit_date": {
                                        "type": "string",
                                        "description": "ISO date of last meaningful edit.",
                                    },
                                    "days_since_last_edit": {
                                        "type": "integer",
                                        "description": (
                                            "Number of days since last meaningful edit."
                                        ),
                                    },
                                    "last_edit_commit_hash": {"type": "string"},
                                    "last_edit_author": {"type": "string"},
                                    "last_edit_message": {"type": "string"},
                                    "first_introduced_date": {
                                        "type": "string",
                                        "description": (
                                            "ISO date when the candidate was first added."
                                        ),
                                    },
                                    "bulk_commits_filtered": {
                                        "type": "integer",
                                        "description": (
                                            "Number of bulk/cosmetic commits filtered out."
                                        ),
                                    },
                                    "is_stale": {
                                        "type": "boolean",
                                        "description": (
                                            "True if last meaningful edit > staleness "
                                            "threshold."
                                        ),
                                    },
                                },
                                "required": [
                                    "last_meaningful_edit_date",
                                    "days_since_last_edit",
                                    "is_stale",
                                ],
                            },
                            "layer_3_active_development": {
                                "type": "object",
                                "properties": {
                                    "open_prs": {
                                        "type": "array",
                                        "items": {
                                            "type": "object",
                                            "properties": {
                                                "pr_number": {"type": "integer"},
                                                "title": {"type": "string"},
                                                "url": {"type": "string"},
                                                "overlap_description": {
                                                    "type": "string",
                                                },
                                            },
                                        },
                                        "description": (
                                            "Open PRs that reference this candidate."
                                        ),
                                    },
                                    "recent_branches": {
                                        "type": "array",
                                        "items": {"type": "string"},
                                        "description": (
                                            "Recent branches referencing this candidate."
                                        ),
                                    },
                                    "actively_being_worked_on": {
                                        "type": "boolean",
                                        "description": (
                                            "True if the candidate is actively being "
                                            "worked on."
                                        ),
                                    },
                                },
                                "required": ["actively_being_worked_on"],
                            },
                            "layer_4_static_reachability": {
                                "type": "object",
                                "properties": {
                                    "is_reachable": {
                                        "type": "boolean",
                                        "description": (
                                            "True if any execution path can reach "
                                            "this code."
                                        ),
                                    },
                                    "call_chain": {
                                        "type": "string",
                                        "description": (
                                            "The call chain from entry point to "
                                            "candidate, if reachable."
                                        ),
                                    },
                                    "has_broken_dependencies": {
                                        "type": "boolean",
                                        "description": (
                                            "True if the candidate calls nonexistent "
                                            "code."
                                        ),
                                    },
                                    "framework_exemption": {
                                        "type": "boolean",
                                        "description": (
                                            "True if a framework pattern makes this "
                                            "look dead but may not be."
                                        ),
                                    },
                                    "framework_pattern": {
                                        "type": "string",
                                        "description": (
                                            "Which framework pattern triggered the "
                                            "exemption."
                                        ),
                                    },
                                    "entry_points_checked": {
                                        "type": "array",
                                        "items": {"type": "string"},
                                        "description": (
                                            "List of entry points that were checked."
                                        ),
                                    },
                                },
                                "required": ["is_reachable", "framework_exemption"],
                            },
                            "layer_5_issue_archaeology": {
                                "type": "object",
                                "properties": {
                                    "related_issues": {
                                        "type": "array",
                                        "items": {
                                            "type": "object",
                                            "properties": {
                                                "number": {"type": "integer"},
                                                "title": {"type": "string"},
                                                "url": {"type": "string"},
                                                "status": {"type": "string"},
                                                "sentiment": {"type": "string"},
                                            },
                                        },
                                    },
                                    "pr_comments_mentioning": {
                                        "type": "array",
                                        "items": {"type": "string"},
                                    },
                                    "commit_messages_mentioning": {
                                        "type": "array",
                                        "items": {"type": "string"},
                                    },
                                    "inline_annotations": {
                                        "type": "array",
                                        "items": {
                                            "type": "object",
                                            "properties": {
                                                "annotation": {"type": "string"},
                                                "implication": {"type": "string"},
                                            },
                                        },
                                    },
                                    "code_owner": {
                                        "type": "string",
                                        "description": (
                                            "Code owner for the candidate's files, "
                                            "if CODEOWNERS exists."
                                        ),
                                    },
                                    "overall_sentiment": {
                                        "type": "string",
                                        "enum": [
                                            "supports_removal",
                                            "opposes_removal",
                                            "ambiguous",
                                            "no_discussion",
                                        ],
                                    },
                                },
                                "required": ["overall_sentiment"],
                            },
                            "layer_6_test_coverage": {
                                "type": "object",
                                "properties": {
                                    "tests_reference_candidate": {
                                        "type": "boolean",
                                        "description": (
                                            "True if any test references this candidate."
                                        ),
                                    },
                                    "test_files": {
                                        "type": "array",
                                        "items": {
                                            "type": "object",
                                            "properties": {
                                                "file": {"type": "string"},
                                                "reference_type": {
                                                    "type": "string",
                                                    "enum": [
                                                        "direct_test",
                                                        "fixture",
                                                        "incidental_import",
                                                    ],
                                                },
                                                "is_stale": {"type": "boolean"},
                                            },
                                        },
                                    },
                                    "coverage_percentage": {
                                        "type": "string",
                                        "description": (
                                            "Coverage % on candidate lines, or "
                                            "'unavailable'."
                                        ),
                                    },
                                    "test_files_needing_update": {
                                        "type": "array",
                                        "items": {"type": "string"},
                                        "description": (
                                            "Test files that would break if candidate "
                                            "is removed."
                                        ),
                                    },
                                },
                                "required": ["tests_reference_candidate"],
                            },
                            "layer_7_runtime_signals": {
                                "type": "object",
                                "properties": {
                                    "flag_platform_available": {"type": "boolean"},
                                    "flag_evaluation_count": {
                                        "type": "string",
                                        "description": (
                                            "Number of flag evaluations in last 90 "
                                            "days, or 'unavailable'."
                                        ),
                                    },
                                    "apm_available": {"type": "boolean"},
                                    "apm_invocation_count": {
                                        "type": "string",
                                        "description": (
                                            "Invocation count from APM, or "
                                            "'unavailable'."
                                        ),
                                    },
                                    "referenced_in_infra": {
                                        "type": "boolean",
                                        "description": (
                                            "True if referenced in deployment/infra/DB "
                                            "configs."
                                        ),
                                    },
                                    "infra_files": {
                                        "type": "array",
                                        "items": {"type": "string"},
                                        "description": (
                                            "Infra/deploy files referencing the "
                                            "candidate."
                                        ),
                                    },
                                    "unavailable_checks": {
                                        "type": "array",
                                        "items": {"type": "string"},
                                        "description": (
                                            "What was unavailable and why."
                                        ),
                                    },
                                },
                                "required": [
                                    "flag_platform_available",
                                    "apm_available",
                                    "referenced_in_infra",
                                ],
                            },
                            "layer_8_external_consumers": {
                                "type": "object",
                                "properties": {
                                    "is_exported": {
                                        "type": "boolean",
                                        "description": (
                                            "True if the symbol is exported/public."
                                        ),
                                    },
                                    "in_published_package": {
                                        "type": "boolean",
                                        "description": (
                                            "True if the repo publishes a package "
                                            "containing this symbol."
                                        ),
                                    },
                                    "external_consumers_found": {
                                        "type": "array",
                                        "items": {"type": "string"},
                                        "description": (
                                            "External repos or services that consume "
                                            "this symbol."
                                        ),
                                    },
                                    "is_api_endpoint": {
                                        "type": "boolean",
                                        "description": (
                                            "True if the candidate is an API endpoint."
                                        ),
                                    },
                                },
                                "required": [
                                    "is_exported",
                                    "in_published_package",
                                    "is_api_endpoint",
                                ],
                            },
                        },
                        "required": [
                            "layer_1_reconfirm",
                            "layer_2_git_staleness",
                            "layer_3_active_development",
                            "layer_4_static_reachability",
                            "layer_5_issue_archaeology",
                            "layer_6_test_coverage",
                            "layer_7_runtime_signals",
                            "layer_8_external_consumers",
                        ],
                    },
                    "confidence": {
                        "type": "string",
                        "enum": ["EXEMPT", "HIGH", "MEDIUM", "LOW"],
                        "description": (
                            "Overall confidence that this candidate is truly dead code."
                        ),
                    },
                    "summary": {
                        "type": "string",
                        "description": "Human-readable 2-3 sentence verdict.",
                    },
                    "blockers": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "List of blocker reasons if confidence is LOW."
                        ),
                    },
                    "suggested_pr_title": {
                        "type": "string",
                        "description": (
                            "Suggested PR title for removal (if HIGH or MEDIUM)."
                        ),
                    },
                    "suggested_pr_description": {
                        "type": "string",
                        "description": (
                            "One-paragraph PR description for removal "
                            "(if HIGH or MEDIUM)."
                        ),
                    },
                    "exempt_reason": {
                        "type": "string",
                        "description": (
                            "Why the detection was wrong (if EXEMPT)."
                        ),
                    },
                    "detection_improvement_suggestion": {
                        "type": "string",
                        "description": (
                            "How to improve detection rules to avoid this false "
                            "positive (if EXEMPT)."
                        ),
                    },
                },
                "required": [
                    "candidate_id",
                    "layer_results",
                    "confidence",
                    "summary",
                ],
            },
        },
        "patterns_observed": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Cross-candidate patterns noticed during validation.",
        },
    },
    "required": ["candidates"],
}

# ---------------------------------------------------------------------------
# Prompt template
# ---------------------------------------------------------------------------
# The double-braces (e.g. ``{{file_path}}``) are literal in the prompt text
# and instruct the Devin session what shell commands to run.  The single-
# brace placeholders (e.g. ``{candidate_block}``) are filled in by Python's
# ``str.format()`` before the prompt is sent.

_VALIDATION_PROMPT = """\
You are validating whether a piece of suspected dead code is truly safe to \
remove. You will run 8 validation layers against it, collect evidence, and \
produce a structured verdict.

Think like a cautious junior engineer who's been told "figure out if this is \
safe to delete." Check everything, assume nothing. False positives \
(accidentally flagging live code for removal) are far worse than false \
negatives (leaving dead code in place). When in doubt, say "not confident \
enough to remove."

## The Candidates

{candidate_block}

## Repository Context

The repository is already cloned and available. Use git, grep, and any \
language-appropriate tooling to investigate.

## Validation Layers

Run ALL 8 layers below, in order, for EACH candidate. For each layer, \
record what you checked, what you found, and your conclusion. If a layer \
requires access you don't have (e.g., no APM platform), note it as \
unavailable and move on.

---

### Layer 1: Re-Confirm the Detection

The initial identification may have matched a comment, string literal, test \
fixture, or documentation rather than actual live code.

Do this:
- Open each file where this candidate was flagged. Read the surrounding 20 \
  lines of context.
- Determine: is this a real code reference, or is the match inside a \
  comment, string, doc file, test mock, or config definition?
- If the original detection was regex-based, try to verify with stronger \
  analysis. For example, check if the match is actually a function call vs. \
  just a variable name that happens to contain the pattern.
- For feature flags specifically: identify the flag SDK being used (e.g., \
  LaunchDarkly `variation()`, custom `isFeatureEnabled()`). Confirm the \
  match is an actual evaluation call.
- Search the entire codebase for other references to this symbol that \
  weren't in the original detection. If you find more, note them.

Record:
- confirmed: true/false
- method used to verify
- if false, explain why (matched a comment, string, etc.)
- any additional files found that also reference this candidate

**If you cannot confirm the detection is real code, STOP HERE. Mark this \
candidate as EXEMPT (false positive) and skip the remaining layers. \
Explain why.**

---

### Layer 2: Git History Staleness

Determine when this code was last meaningfully changed. "Meaningfully" \
means: ignore bulk reformats, linter auto-fixes, mass renames, dependency \
bumps, and merge commits.

Do this:
- Run `git log --follow --format="%H|%ai|%an|%s" --diff-filter=M -- \
  {{file_path}}` for each file.
- For each commit, check how many files it touched: \
  `git diff-tree --no-commit-id --name-only -r {{commit_hash}} | wc -l`. \
  If it touched more than {bulk_commit_threshold} files, it's likely a \
  bulk operation --- filter it out.
- Also filter commits whose messages match (case-insensitive): format, \
  lint, prettier, black, autopep8, rename, bump, upgrade, auto-generated, \
  codegen, merge.
- From the filtered list, find the most recent commit. That's the "last \
  meaningful edit."
- For more precision, use `git log -S "{{symbol_name}}" \
  --format="%H|%ai|%an|%s"` (pickaxe search) to find commits that \
  specifically added or removed the candidate symbol text.
- Also find when the candidate was FIRST introduced: \
  `git log --all -S "{{symbol_name}}" --format="%ai" --reverse | head -1`.

Record:
- last meaningful edit date and how many days ago
- the commit hash, author, and message of that edit
- when the candidate was first introduced
- how many bulk/cosmetic commits were filtered out
- whether the code is stale (last meaningful edit > {staleness_days} \
  days ago)

---

### Layer 3: Active Development Cross-Reference

Check if anyone is currently working on or near this code. This is a \
critical safety check.

Do this:
- Use the GitHub API (or equivalent) to fetch all open PRs. Filter to PRs \
  with commits in the last {pr_lookback_days} days.
- For each open PR, get its changed files list. Check if any of the \
  candidate's files appear.
- Also search each PR's diff for the candidate's symbol name --- it might \
  be referenced in a different file.
- Check recent remote branches: \
  `git branch -r --sort=-committerdate \
  --format='%(refname:short) %(committerdate:iso)' | head -50`. \
  For branches with recent commits (last {branch_lookback_days} days), \
  run `git diff main...{{branch}} --name-only` and check for file overlap \
  or symbol references.
- Don't skip draft PRs or branches named wip/draft/experiment/spike --- \
  these indicate active exploration.

Record:
- list of open PRs that reference this candidate (PR number, title, URL, \
  how it overlaps)
- list of recent branches that reference this candidate
- whether the candidate is actively being worked on (true/false)

**If actively being worked on -> this is a BLOCKER. Note it clearly. \
Confidence cannot be higher than LOW regardless of what other layers find.**

---

### Layer 4: Static Reachability Analysis

Determine whether any execution path in the application can reach this code.

Do this:
- Identify the language and framework by checking dependency manifests \
  (package.json, requirements.txt, build.gradle, go.mod, etc.).
- Search the entire codebase for direct references to the candidate \
  symbol: `grep -rn "{{symbol_name}}" --include="*.{{ext}}" .` \
  (exclude test directories, comments, strings where possible).
- For each reference found, trace upward: is THAT code called by \
  something else? Repeat until you reach a known entry point or run out \
  of callers.
- Check against known entry points: main functions, HTTP route handlers \
  (@app.route, router.get, @GetMapping, file-based routing), CLI \
  commands, event handlers (@celery_task, message queue consumers), \
  scheduled jobs (cron, @Scheduled), build/deploy scripts, exported \
  library APIs.
- Check for broken dependencies: does the candidate code call functions \
  that no longer exist, import modules that have been deleted, or \
  reference config keys that are gone? If so, it would crash if executed \
  --- strong evidence it's dead.
- **CRITICAL --- check for dynamic/framework patterns that make code look \
  dead but isn't:**
  - Convention-based routing (Next.js pages/, Rails controllers/)
  - Dependency injection (@Bean, @Injectable, @Provides)
  - Decorator-registered handlers (@app.route, @pytest.fixture, \
    @celery.task)
  - Reflection/dynamic dispatch (getattr, Class.forName, obj[key]())
  - Serialization hooks (__reduce__, toJSON, fromJSON)
  - ORM model methods (save, clean, __str__)
  - Template rendering (Jinja macros, JSX components referenced in \
    templates)
  - Plugin/extension registries
  - Signal/slot patterns (Django signals, EventEmitter)
  If any of these patterns apply, mark it as a framework exemption.

Record:
- whether the candidate is reachable from any entry point (and if so, \
  what's the call chain)
- whether the candidate has broken dependencies (calls nonexistent code)
- whether a framework exemption was triggered (and which pattern)
- list of entry points checked

**If a framework exemption is triggered -> this is a BLOCKER. Confidence \
cannot be higher than LOW.**

---

### Layer 5: Issue & Discussion Archaeology

Check whether humans have discussed this code recently in a way that \
suggests it's still needed or intentionally kept.

Do this:
- Search GitHub issues (open AND recently closed, last {issue_lookback_days} \
  days) for the candidate's symbol name.
- Search PR comments and review comments for mentions.
- Search commit messages: \
  `git log --all --oneline --grep="{{symbol_name}}" --since="6 months ago"`
- In the candidate's source files, scan the lines near the candidate code \
  (within 10 lines above and below, or within the same function) for \
  these annotations:
  - TODO, FIXME, HACK, NOTE --- read the full comment for context
  - DEPRECATED, @deprecated --- supports removal
  - "DO NOT DELETE", "KEEP", "INTENTIONAL", "do not remove" --- BLOCKER
  - "re-enable", "will use", "needed for", "planned" --- BLOCKER
  - noqa, nolint, @SuppressWarnings --- code intentionally kept despite \
    warnings
- Check for a CODEOWNERS file. If it exists, find who owns the \
  candidate's files --- they should review any removal PR.

Record:
- issues mentioning the candidate (number, title, URL, status, whether \
  they support or oppose removal)
- PR comments mentioning it
- commit messages mentioning it
- inline annotations found and what they imply
- code owner for the files
- overall sentiment: supports_removal / opposes_removal / ambiguous / \
  no_discussion

**If inline annotations say "keep", "do not remove", "re-enable", or \
"needed for" -> BLOCKER. If an open issue states the code is planned for \
future use -> BLOCKER.**

---

### Layer 6: Test Coverage & Test References

Check whether tests reference or exercise this code.

Do this:
- Search test directories (tests/, test/, __tests__/, spec/, and files \
  matching *test*, *spec*) for the candidate symbol: \
  `grep -rn "{{symbol_name}}" tests/ __tests__/ spec/ \
  --include="*test*" --include="*spec*"`
- For each test file that references the candidate, classify the \
  reference: is it testing the candidate directly, using it as a fixture, \
  or just importing its module incidentally?
- For test files that directly reference the candidate, check their git \
  staleness using the same logic as Layer 2. Are the tests themselves \
  stale (last meaningful edit > {staleness_days} days)?
- If code coverage reports exist in the repo (look for coverage/, \
  htmlcov/, .coverage, lcov.info, coverage.xml), check the coverage \
  percentage on the candidate's lines. 0% coverage = never executed \
  in tests.
- Note which test files would break if the candidate is removed --- \
  these need to be included in any removal PR.

Record:
- whether tests reference the candidate
- list of test files and whether each is stale
- coverage percentage on candidate lines (or "unavailable")
- which test files would need updating if the candidate is removed

**If active (non-stale) tests with >0% coverage exist for the candidate \
-> confidence cannot exceed MEDIUM.**

---

### Layer 7: Runtime & Deployment Signals (Best-Effort)

Check for evidence of whether this code actually runs in production. \
This layer is best-effort --- if you don't have access to the relevant \
systems, note it and skip.

Do this:
- If a feature flag platform is in use, try to check: how many times was \
  this flag evaluated in the last {flag_eval_days} days? What's the \
  current flag value? Is it permanently set to one value? Check for API \
  access or for flag config files committed to the repo.
- If APM/observability tools are configured, try to check invocation \
  counts.
- Search infrastructure and deployment files for references to the \
  candidate: .github/workflows/, .circleci/, Jenkinsfile, Makefile, \
  Dockerfile, docker-compose*.yml, terraform/, k8s/, helm/, .env*, \
  config/, settings/.
- Search database migration files and model definitions for references.

Record:
- whether flag platform data is available, and if so, evaluation count
- whether APM data is available, and if so, invocation count
- whether the candidate is referenced in infra/deployment/DB configs
- what was unavailable and why

**If non-zero production flag evaluations or APM invocations -> BLOCKER.**

---

### Layer 8: Cross-Repository & External Consumers

Check if this code is consumed by anything outside this repository.

Do this:
- Determine if the candidate is exported/public: check module.exports, \
  export, __all__, public class/method, capitalized Go name.
- If the repo publishes a package (check package.json name, \
  pyproject.toml name, etc.), note it.
- If possible, search other repositories in the organization for imports \
  or references to the candidate symbol (use GitHub org-wide code search \
  or Sourcegraph if available).
- If the candidate is an API endpoint, search for the endpoint path in \
  other repos and in API gateway/reverse proxy configs.

Record:
- whether the symbol is exported
- whether it's in a published package
- any external consumers found
- whether it's an API endpoint with potential external callers

**If external consumers are found -> BLOCKER.**

---

## Confidence Scoring

After all 8 layers, compute a confidence level:

**EXEMPT:** Layer 1 failed (detection was a false positive). Stop processing.

**HIGH (recommend auto-removal PR):** Zero blockers from any layer. Code \
is stale, unreachable, undiscussed, untested (or tests are also stale), \
no runtime activity, no external consumers. At least 5 of the 8 layers \
actively corroborate that the code is dead.

**MEDIUM (recommend draft PR for human review):** Zero hard blockers. Some \
ambiguity exists --- maybe tests reference it but are stale, or an issue \
mentions it with unclear sentiment, or one layer was unavailable. The \
preponderance of evidence says dead.

**LOW (report only, do not recommend a PR):** One or more blocker signals \
fired. Explain which blockers and what would need to change.

---

## Output

Produce your results in the structured output. For each candidate fill in \
ALL layer results (layer_1_reconfirm through layer_8_external_consumers), \
the confidence level, a human-readable summary, and---where applicable---\
a suggested PR title/description, blockers, or exempt reason.

Also note any cross-candidate patterns in the patterns_observed array.
"""


# ---------------------------------------------------------------------------
# Configuration defaults
# ---------------------------------------------------------------------------

DEFAULT_CONFIG: dict[str, int] = {
    "staleness_days": 365,
    "pr_lookback_days": 90,
    "branch_lookback_days": 90,
    "bulk_commit_threshold": 50,
    "issue_lookback_days": 180,
    "flag_eval_days": 90,
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fmt_elapsed(seconds: float) -> str:
    """Format elapsed seconds as ``Xm YYs`` or ``Ys``."""
    m, s = divmod(int(seconds), 60)
    return f"{m}m{s:02d}s" if m else f"{s}s"


def _extract_json_block(text: str) -> dict[str, Any] | None:
    """Extract the first JSON code-fence block from *text*.

    Returns the parsed dict, or ``None`` if no valid JSON block is found.
    """
    # Try ```json ... ``` first
    pattern = r"```(?:json)?\s*\n(\{.*?\})\s*\n```"
    match = re.search(pattern, text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass

    # Fallback: try to find a raw JSON object
    brace_start = text.find("{")
    if brace_start != -1:
        depth = 0
        for i in range(brace_start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[brace_start : i + 1])
                    except json.JSONDecodeError:
                        pass
                    break
    return None


# ---------------------------------------------------------------------------
# Batching helpers
# ---------------------------------------------------------------------------


def _candidate_sort_key(candidate: dict[str, Any]) -> str:
    """Return a grouping key for a candidate (directory or file path)."""
    file_path = candidate.get("file", "")
    # Group by directory so related files end up together
    parts = file_path.rsplit("/", 1)
    return parts[0] if len(parts) > 1 else ""


def _escape_braces(text: str) -> str:
    """Escape ``{`` and ``}`` so they survive ``str.format()``."""
    return text.replace("{", "{{").replace("}", "}}")


def _format_candidate_block(
    candidates: list[dict[str, Any]], category_label: str
) -> str:
    """Format a list of candidates into the block that goes into the prompt.

    All candidate-supplied text (code snippets, reasoning, flag names, etc.)
    is brace-escaped so that a subsequent ``str.format()`` call on the
    prompt template does not misinterpret Python dicts, f-strings, or JS
    objects as format placeholders.
    """
    lines: list[str] = []
    for i, cand in enumerate(candidates, 1):
        lines.append(f"### Candidate {i}")
        lines.append(f"- **ID:** {_escape_braces(str(cand.get('id', 'unknown')))}")
        lines.append(f"- **Category:** {_escape_braces(category_label)}")
        lines.append(f"- **File:** {_escape_braces(str(cand.get('file', 'unknown')))}")
        lines.append(f"- **Line:** {cand.get('line', 'unknown')}")

        # Include type-specific fields
        if "flag_name" in cand:
            lines.append(
                f"- **Flag/Symbol name:** {_escape_braces(cand['flag_name'])}"
            )
        if "pattern_type" in cand:
            lines.append(
                f"- **Pattern type:** {_escape_braces(cand['pattern_type'])}"
            )
        if "category" in cand:
            lines.append(
                f"- **Sub-category:** {_escape_braces(cand['category'])}"
            )

        snippet = cand.get("code_snippet", "")
        if snippet:
            lines.append(
                f"- **Code snippet:**\n```\n{_escape_braces(snippet)}\n```"
            )

        reasoning = cand.get("reasoning", "")
        if reasoning:
            lines.append(
                f"- **Detection reasoning:** {_escape_braces(reasoning)}"
            )

        lines.append("")
    return "\n".join(lines)


def group_candidates(
    findings: dict[str, Any],
    max_batch_size: int = 5,
) -> list[tuple[str, list[dict[str, Any]]]]:
    """Group Part 1 findings into batches for DevinAPI sessions.

    Related candidates (same directory / feature area) are grouped together
    up to *max_batch_size*.  Returns a list of ``(category_label, [candidates])``
    tuples.
    """
    batches: list[tuple[str, list[dict[str, Any]]]] = []

    for category, label in [
        ("feature_flags", "feature_flag"),
        ("dead_code", "dead_code"),
        ("tech_debt", "tech_debt"),
    ]:
        items = findings.get(category, [])
        if not items:
            continue

        # Sort by directory to cluster related candidates
        sorted_items = sorted(items, key=_candidate_sort_key)

        # Build sub-groups by directory
        groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for item in sorted_items:
            key = _candidate_sort_key(item)
            groups[key].append(item)

        # Split each sub-group into chunks of max_batch_size
        for _dir_key, group_items in groups.items():
            for offset in range(0, len(group_items), max_batch_size):
                chunk = group_items[offset : offset + max_batch_size]
                batches.append((label, chunk))

    return batches


# ---------------------------------------------------------------------------
# Result merging
# ---------------------------------------------------------------------------


def _merge_validation_into_findings(
    findings: dict[str, Any],
    validation_map: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Merge per-candidate validation results back into the Part 1 structure.

    Mutates *findings* in place and also returns it for convenience.
    """
    for category in ("feature_flags", "dead_code", "tech_debt"):
        for item in findings.get(category, []):
            finding_id = item.get("id", "")
            if finding_id in validation_map:
                vdata = validation_map[finding_id]
                item["verification_status"] = _status_from_confidence(
                    vdata.get("confidence", "LOW")
                )
                item["validation"] = vdata

    return findings


def _status_from_confidence(confidence: str) -> str:
    """Map a confidence level to the ``verification_status`` vocabulary."""
    return {
        CONFIDENCE_EXEMPT: "false_positive",
        CONFIDENCE_HIGH: "verified",
        CONFIDENCE_MEDIUM: "needs_review",
        CONFIDENCE_LOW: "needs_review",
    }.get(confidence, "unverified")


# ---------------------------------------------------------------------------
# Summary / report helpers
# ---------------------------------------------------------------------------


def build_summary_report(
    validation_map: dict[str, dict[str, Any]],
    all_patterns: list[str],
) -> dict[str, Any]:
    """Build the final aggregated report from validated findings."""
    counts: dict[str, int] = {
        CONFIDENCE_HIGH: 0,
        CONFIDENCE_MEDIUM: 0,
        CONFIDENCE_LOW: 0,
        CONFIDENCE_EXEMPT: 0,
    }
    high_candidates: list[dict[str, Any]] = []
    medium_candidates: list[dict[str, Any]] = []
    low_candidates: list[dict[str, Any]] = []
    exempt_candidates: list[dict[str, Any]] = []

    for _cid, vdata in validation_map.items():
        conf = vdata.get("confidence", "LOW")
        counts[conf] = counts.get(conf, 0) + 1
        entry = {
            "candidate_id": vdata.get("candidate_id", _cid),
            "summary": vdata.get("summary", ""),
        }
        if conf == CONFIDENCE_HIGH:
            entry["suggested_pr_title"] = vdata.get("suggested_pr_title", "")
            entry["suggested_pr_description"] = vdata.get(
                "suggested_pr_description", ""
            )
            high_candidates.append(entry)
        elif conf == CONFIDENCE_MEDIUM:
            entry["suggested_pr_title"] = vdata.get("suggested_pr_title", "")
            entry["suggested_pr_description"] = vdata.get(
                "suggested_pr_description", ""
            )
            medium_candidates.append(entry)
        elif conf == CONFIDENCE_LOW:
            entry["blockers"] = vdata.get("blockers", [])
            low_candidates.append(entry)
        else:
            entry["exempt_reason"] = vdata.get("exempt_reason", "")
            entry["detection_improvement_suggestion"] = vdata.get(
                "detection_improvement_suggestion", ""
            )
            exempt_candidates.append(entry)

    return {
        "confidence_counts": counts,
        "high_confidence": high_candidates,
        "medium_confidence": medium_candidates,
        "low_confidence": low_candidates,
        "exempt": exempt_candidates,
        "patterns_observed": all_patterns,
        "recommendations": _generate_recommendations(
            counts, all_patterns
        ),
    }


def _generate_recommendations(
    counts: dict[str, int],
    patterns: list[str],
) -> list[str]:
    """Generate human-readable recommendations for the team."""
    recs: list[str] = []
    if counts.get(CONFIDENCE_HIGH, 0) > 0:
        recs.append(
            f"{counts[CONFIDENCE_HIGH]} candidate(s) are HIGH confidence --- "
            "consider creating auto-removal PRs for these."
        )
    if counts.get(CONFIDENCE_MEDIUM, 0) > 0:
        recs.append(
            f"{counts[CONFIDENCE_MEDIUM]} candidate(s) are MEDIUM confidence --- "
            "create draft PRs for human review."
        )
    if counts.get(CONFIDENCE_LOW, 0) > 0:
        recs.append(
            f"{counts[CONFIDENCE_LOW]} candidate(s) are LOW confidence --- "
            "do not remove without further investigation."
        )
    if counts.get(CONFIDENCE_EXEMPT, 0) > 0:
        recs.append(
            f"{counts[CONFIDENCE_EXEMPT]} candidate(s) were false positives --- "
            "review detection rules to reduce noise."
        )
    for pattern in patterns:
        recs.append(f"Pattern: {pattern}")
    return recs


# ---------------------------------------------------------------------------
# Main validator class
# ---------------------------------------------------------------------------


class LegacyCodeValidator:
    """Validate scanner findings via a single Devin session.

    Uses the same single-session pattern as Phase 1:

    1. **Setup** --- create session with neutral prompt, wait until ready.
    2. **Batch prompts** --- ``send_message()`` for each batch of
       related candidates.
    3. **Parse results** --- extract JSON from Devin's text responses
       via the v1 API (which includes the full conversation history).
    4. **Sleep** --- send the session to sleep when all batches are done.

    ``send_message()`` uses the v1 API which works with ``cog_`` keys.
    """

    # The follow-up message sent to unblock a waiting session.
    _NUDGE_MESSAGE = (
        "Please continue with the validation analysis and produce your "
        "results as a JSON block. If you need any clarification, proceed "
        "with best-effort analysis using the information already available "
        "in the repository."
    )

    def __init__(
        self,
        client: DevinAPIClient,
        *,
        config: dict[str, int] | None = None,
    ) -> None:
        self._client = client
        self._config = {**DEFAULT_CONFIG, **(config or {})}

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def validate(
        self,
        findings: dict[str, Any],
        *,
        poll_interval: int = 15,
        poll_timeout: int = 900,
        max_acu_limit: int | None = None,
        progress_tracker_factory: Callable[..., Callable[[dict[str, Any]], None]] | None = None,
        max_batch_size: int = 5,
    ) -> dict[str, Any]:
        """Validate a set of Part 1 findings.

        Creates a **single** Devin session, then sends each batch of
        candidates as a follow-up message via ``send_message()``.

        Args:
            findings: The enriched structured output from the scanner.
            poll_interval: Seconds between status polls.
            poll_timeout: Max seconds to wait per prompt round.
            max_acu_limit: Optional ACU cap for the session.
            progress_tracker_factory: Optional callable
                ``(batch_idx, total_batches, batch_size) -> callback``
                that returns a per-batch progress callback.
            max_batch_size: Maximum candidates per batch prompt.

        Returns:
            The original *findings* dict, enriched with validation data and
            an additional ``"validation_report"`` key containing the summary.
        """
        repo = findings.get("repo", findings.get("meta", {}).get("repo", ""))

        batches = group_candidates(findings, max_batch_size=max_batch_size)
        total_batches = len(batches)

        if total_batches == 0:
            print("[validator] No candidates to validate.")
            findings["validation_report"] = build_summary_report({}, [])
            return findings

        total_candidates = sum(len(b[1]) for b in batches)
        print(
            f"[validator] {total_batches} batch(es) to validate across "
            f"{total_candidates} candidate(s)."
        )

        # ---- Create session with neutral setup prompt ----
        setup_prompt = (
            f"You are a dead-code validation assistant for the repository "
            f"**{repo}**.  Wait for my instructions before doing anything."
        )

        print(f"[validator] Creating session for {repo} ...")
        session = self._client.create_session(
            prompt=setup_prompt,
            repos=[repo] if repo else None,
            tags=["dead-code-validation", "automated"],
            title=f"Dead-code validation: {repo}",
            max_acu_limit=max_acu_limit,
        )
        session_id = session["session_id"]
        session_url = session.get("url", "")
        print(f"[validator] Session: {session_id}")
        print(f"[validator] URL: {session_url}")

        # Wait for session to be ready
        print("[validator] Waiting for session to initialise ...")
        phase0_start = time.monotonic()

        def _setup_status(sess: dict[str, Any]) -> None:
            elapsed = time.monotonic() - phase0_start
            status = sess.get("status", "")
            detail = sess.get("status_detail", "")
            print(
                f"  [{_fmt_elapsed(elapsed)}] {status}"
                f" ({detail})  | Initialising session ..."
            )

        self._client.poll_session(
            session_id,
            interval=poll_interval,
            timeout=poll_timeout,
            on_update=_setup_status,
        )

        # ---- Send batch validation prompts ----
        validation_map: dict[str, dict[str, Any]] = {}
        all_patterns: list[str] = []

        for batch_idx, (category_label, batch_candidates) in enumerate(
            batches, 1
        ):
            ids = [c.get("id", "?") for c in batch_candidates]
            print(
                f"\n[validator] --- Batch {batch_idx}/{total_batches}: "
                f"{category_label} ({len(batch_candidates)} candidate(s): "
                f"{', '.join(ids)}) ---"
            )

            prompt = self._build_prompt(category_label, batch_candidates)

            # Send batch prompt as a follow-up message
            self._client.send_message(session_id, prompt)

            # Build per-batch progress tracker if factory provided.
            tracker = (
                progress_tracker_factory(
                    batch_idx, total_batches, len(batch_candidates)
                )
                if progress_tracker_factory is not None
                else None
            )

            # Wait for Devin to finish processing this batch
            batch_start = time.monotonic()

            def _batch_status(
                sess: dict[str, Any],
                _bidx: int = batch_idx,
                _btot: int = total_batches,
                _ncand: int = len(batch_candidates),
                _bstart: float = batch_start,
            ) -> None:
                elapsed = time.monotonic() - _bstart
                status = sess.get("status", "")
                detail = sess.get("status_detail", "")
                print(
                    f"  [{_fmt_elapsed(elapsed)}] {status}"
                    f" ({detail})"
                    f"  | Batch {_bidx}/{_btot}"
                    f"  | Candidates: {_ncand}"
                )

            # Use the factory tracker if available, else the simple one
            on_update_cb = tracker if tracker is not None else _batch_status

            self._client.poll_session(
                session_id,
                interval=poll_interval,
                timeout=poll_timeout,
                on_update=on_update_cb,
                expect_running_first=True,
            )

            # Fetch conversation via V1 to get Devin's latest text response
            v1_session = self._client.get_session_v1(session_id)
            batch_result = self._parse_batch_response(v1_session)

            if batch_result is None:
                print(
                    f"[validator]   WARNING: no parseable output for batch "
                    f"{batch_idx}; marking candidates as unverified."
                )
                for cand in batch_candidates:
                    validation_map[cand["id"]] = {
                        "candidate_id": cand["id"],
                        "confidence": CONFIDENCE_LOW,
                        "summary": (
                            "Validation session did not produce output "
                            "for this batch."
                        ),
                        "blockers": [
                            "Could not extract validation results from "
                            "session response."
                        ],
                        "layer_results": {},
                    }
                continue

            # Parse per-candidate results
            for vresult in batch_result.get("candidates", []):
                cid = vresult.get("candidate_id", "")
                if cid:
                    validation_map[cid] = vresult

            all_patterns.extend(batch_result.get("patterns_observed", []))

            n_parsed = len(batch_result.get("candidates", []))
            print(
                f"[validator]   Batch {batch_idx} complete. "
                f"Results for {n_parsed} candidate(s)."
            )

        # ---- Send session to sleep ----
        print("[validator] Sending session to sleep ...")
        self._client.send_message(session_id, "sleep")

        # Merge results back into findings
        _merge_validation_into_findings(findings, validation_map)

        # Build aggregate report
        report = build_summary_report(validation_map, all_patterns)
        findings["validation_report"] = report

        # Print summary
        self._print_summary(report)

        return findings

    # ------------------------------------------------------------------
    # Response parsing
    # ------------------------------------------------------------------

    @staticmethod
    def _last_devin_message(session: dict[str, Any]) -> str:
        """Return the text of the most recent ``devin_message`` in the
        V1 session response's ``messages`` list.
        """
        messages = session.get("messages") or []
        for msg in reversed(messages):
            if msg.get("type") == "devin_message":
                return msg.get("message", "")
        return ""

    def _parse_batch_response(
        self, session: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Extract batch validation results from the session response.

        Uses the V1 session response which includes the full
        ``messages`` list.  Tries structured_output first, then
        parses JSON from Devin's last message.

        Returns the parsed dict with a ``candidates`` key, or ``None``
        if nothing could be extracted.
        """
        # Try structured output first
        structured = session.get("structured_output")
        if structured is not None:
            if structured.get("candidates") is not None:
                return structured

        # Fallback: parse JSON from Devin's last message
        last_text = self._last_devin_message(session)
        if last_text:
            parsed = _extract_json_block(last_text)
            if parsed and "candidates" in parsed:
                return parsed

        return None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_prompt(
        self,
        category_label: str,
        candidates: list[dict[str, Any]],
    ) -> str:
        """Fill in the prompt template for a batch of candidates."""
        candidate_block = _format_candidate_block(candidates, category_label)
        return _VALIDATION_PROMPT.format(
            candidate_block=candidate_block,
            staleness_days=self._config["staleness_days"],
            pr_lookback_days=self._config["pr_lookback_days"],
            branch_lookback_days=self._config["branch_lookback_days"],
            bulk_commit_threshold=self._config["bulk_commit_threshold"],
            issue_lookback_days=self._config["issue_lookback_days"],
            flag_eval_days=self._config["flag_eval_days"],
        )

    @staticmethod
    def _print_summary(report: dict[str, Any]) -> None:
        """Print a human-readable summary table."""
        counts = report.get("confidence_counts", {})
        print("\n" + "=" * 60)
        print("VALIDATION SUMMARY")
        print("=" * 60)
        print(f"  HIGH confidence (auto-remove):   {counts.get('HIGH', 0)}")
        print(f"  MEDIUM confidence (draft PR):    {counts.get('MEDIUM', 0)}")
        print(f"  LOW confidence (report only):    {counts.get('LOW', 0)}")
        print(f"  EXEMPT (false positive):         {counts.get('EXEMPT', 0)}")

        recs = report.get("recommendations", [])
        if recs:
            print("\n  Recommendations:")
            for rec in recs:
                print(f"    - {rec}")
        print("=" * 60)

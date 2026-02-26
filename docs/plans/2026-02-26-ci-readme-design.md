# CI/CD + Professional README Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add GitHub Actions CI/CD for Python and rewrite README.md to be professional and visually distinctive.

**Architecture:** Two standalone file changes — a new CI workflow and a README rewrite. No code changes.

**Tech Stack:** GitHub Actions (checkout@v4, setup-python@v5), Python 3.11/3.12/3.13, pytest, ruff

---

### Task 1: Create GitHub Actions CI workflow

**Files:**
- Create: `.github/workflows/ci.yml`

**Step 1: Create the workflow directory**

```bash
mkdir -p .github/workflows
```

**Step 2: Create `.github/workflows/ci.yml`**

```yaml
name: CI

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

jobs:
  lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - name: Install dependencies
        run: pip install -e ".[dev]"
      - name: Lint with ruff
        run: ruff check server/

  test:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        python-version: ["3.11", "3.12", "3.13"]
    steps:
      - uses: actions/checkout@v4
      - name: Set up Python ${{ matrix.python-version }}
        uses: actions/setup-python@v5
        with:
          python-version: ${{ matrix.python-version }}
      - name: Install dependencies
        run: pip install -e ".[dev]"
      - name: Run tests
        run: pytest --tb=short -q
```

**Step 3: Verify the YAML is valid**

```bash
python -c "import yaml; yaml.safe_load(open('.github/workflows/ci.yml'))"
```

If pyyaml is not installed, just visually confirm the file looks correct.

**Step 4: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: add GitHub Actions workflow for lint + test matrix"
```

---

### Task 2: Rewrite README.md

**Files:**
- Modify: `README.md` (full rewrite)

**Step 1: Write the new README.md**

The new README must follow this exact structure. Preserve all technical content (schema tables, tools table, languages table, architecture diagram) but reorganize into this flow:

1. **Title block**: `# code-review-graph` with a one-line tagline below
2. **Badges row**: GitHub stars, license, CI status (using the new workflow), Python version, MCP compatible
3. **Hero section**: The quote line (exactly as written): `It turns Claude from "smart but forgetful tourist" into "local expert who already knows the map."`  — followed by the existing "Why?" comparison table
4. **Features section** (`## Features`): Key capabilities list with emojis — incremental updates, 12+ languages, blast-radius analysis, token-efficient reviews, auto-update hooks, vector embeddings
5. **Quick Start section** (`## Quick Start`): 3 steps — clone+install, add MCP config, run build-graph. Keep the existing install commands and `.mcp.json` snippet
6. **How It Works section** (`## How It Works`): The existing architecture ASCII diagram + component descriptions
7. **Deep Dive section** (`## Deep Dive`): Brief paragraph pointing to `docs/` folder. Link to `docs/USAGE.md` as the starting point. List the key doc files.
8. **Graph Schema section** (`## Graph Schema`): Existing nodes + edges tables (unchanged)
9. **MCP Tools section** (`## MCP Tools`): Existing tools table. Add `embed_graph_tool` and `get_docs_section_tool` which are missing from current table.
10. **Supported Languages section** (`## Supported Languages`): Existing table (unchanged)
11. **Configuration section** (`## Configuration`): Existing `.code-review-graphignore` example
12. **Testing section** (`## Testing`): How to run tests + lint
13. **Contributing section** (`## Contributing`): Existing language addition guide + dev setup
14. **Comparison section** (`## Comparison`): Existing comparison table
15. **License**: MIT
16. **Closing line**: `Built with love for better code reviews`

Key rules:
- The hero quote line MUST be exactly: `It turns Claude from "smart but forgetful tourist" into "local expert who already knows the map."`
- Reference `docs/` folder at least 4 times
- Use emojis in section headers as specified
- Add horizontal rules between major sections
- CI badge URL: `https://github.com/tirth8205/code-review-graph/actions/workflows/ci.yml/badge.svg`

**Step 2: Verify the preserved line is present**

```bash
grep -c "smart but forgetful tourist" README.md
```

Expected: `1`

**Step 3: Verify docs/ references**

```bash
grep -c "docs/" README.md
```

Expected: 4 or more

**Step 4: Commit**

```bash
git add README.md
git commit -m "docs: rewrite README with professional layout and CI badge"
```

---

### Task 3: Final verification

**Step 1: Run tests to confirm nothing broke**

```bash
source .venv/bin/activate && pytest --tb=short -q
```

Expected: 47 passed

**Step 2: Run lint**

```bash
source .venv/bin/activate && ruff check server/
```

Expected: no errors

**Step 3: Verify git log**

```bash
git log --oneline -5
```

Expected: 2 new commits (ci + docs)

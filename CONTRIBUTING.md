# Contributing to Episoda MCP

<img src="https://capsule-render.vercel.app/api?type=rect&color=ff79c6&height=2&width=100%"/>


Thanks for your interest in contributing! This project follows the **Ponytail Philosophy**: keep it minimal, stdlib-only, and zero-bloat.

## Ground Rules

1. **No new dependencies.** If it can't be done with Python's standard library, it probably shouldn't be done here.
2. **Delete more than you add.** The best PRs remove unnecessary code.
3. **YAGNI.** Don't add features "just in case." Only add what's needed right now.

## How to Contribute

1. Fork the repo.
2. Create a feature branch: `git checkout -b feat/your-feature`
3. Make your changes. Keep them focused and small.
4. Test manually with the CLI: `python3 episoda.py stats`
5. Commit with a clear message: `git commit -m "feat: add X"`
6. Push and open a Pull Request.

## What We're Looking For

- Bug fixes
- Performance improvements to SQLite queries
- UI/UX improvements for the `tui` dashboard
- Smarter heuristic conflict detection during memory saves
- Documentation improvements
- Test coverage

## What We Won't Merge

- PRs that add third-party dependencies
- Features that increase complexity without clear justification
- Code that doesn't follow the existing style

## Code Style

- Keep functions short and focused.
- Use descriptive variable names where it matters, short ones where context is obvious.
- No docstrings on internal functions (the code should be self-explanatory).
- Follow the existing formatting patterns.

## Questions?

Open an issue. We'll get back to you.

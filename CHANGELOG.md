# Changelog

All notable changes to Episoda Core MCP will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [v1.1.0] - 2026-09-08

### Fixed
- Corrected database path in README documentation (`~/episoda-core-mcp/memory.db` → `~/engram-mcp/memory.db`)
- Fixed server identity name in JSON-RPC response (`engram-mcp` → `episoda-core-mcp`)
- Updated all stderr log prefixes from `[engram-*]` to `[episoda-*]`
- Removed duplicate Contributing paragraph (merge artifact)
- Corrected Ebbinghaus wording: "exponential time-decay curve" → "inspired time-decay algorithm"
- Removed duplicate `episoda.py` file (byte-identical copy of `engram.py`)

## [v1.0.0] - 2026-07-27

### Added
- Zero-dependency MCP memory server built on Python 3.8+ standard library
- SQLite FTS5 full-text search with BM25 ranking
- Ebbinghaus-inspired auto-decay algorithm for memory importance
- WAL mode concurrency for multi-agent workflows
- Daily automatic SQLite backups
- Conflict surfacing for contradicting memories
- CLI with `--json` export, import/export, interactive TUI dashboard
- Payload bounding (max 8 results, 8,000 char truncation)
- CI pipeline across Python 3.8, 3.10, 3.12
- 18 unit tests covering save, search, dedup, decay, CLI, conflict detection
- Troubleshooting section in README
- Demo GIF recording in README
- Chinese (简体中文) README translation

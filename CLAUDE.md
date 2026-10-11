# CLAUDE.md

## Token discipline

- Never read full bill text, statute text, or other large source documents into
  the conversation. Write a script that extracts or compares what's needed and
  read only its output.
- Read files by line range when you only need part of them. Use grep/rg to
  locate before reading.
- Pipe test, build, and search output through tail/grep/head so no single tool
  result exceeds ~200 lines.
- For any task that requires reading many files or large documents, use a
  subagent and have it return only a concise summary (findings, file paths,
  line numbers), not file contents.
- Default subagents to Sonnet; use Opus only for planning, architecture, and
  reviewing tricky logic.
- At the end of each phase, write decisions and open items to
  `docs/handoff.md` so the next session can start fresh instead of continuing
  a long thread.

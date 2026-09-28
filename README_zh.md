<div align="center">
  <img src="https://capsule-render.vercel.app/api?type=cylinder&color=gradient&customColorList=1,2&height=180&section=header&text=Episoda%20Core%20MCP&fontSize=75&fontAlignY=45&animation=scaleIn&fontColor=ffffff&desc=零外部依赖%20%7C%20纯%20Python%20标准库%20%7C%20艾宾浩斯自愈记忆引擎&descAlignY=65&descAlign=62" width="100%"/>

  <br>

  [![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg?style=for-the-badge)](https://opensource.org/licenses/MIT)
  [![English Docs](https://img.shields.io/badge/English-README-blue?style=for-the-badge)](README.md)
  [![Python 3.8+](https://img.shields.io/badge/Python-3.8%2B-brightgreen?style=for-the-badge&logo=python)](https://www.python.org/)
</div>

<br>

> ⚡ **硬件加速与向量图谱旗舰版**  
> 如果您追求 Apple Silicon AMX 硬件加速（120万+ 向量/秒）与 Obsidian 双链知识图谱，请体验旗舰版：[**Episodai**](https://github.com/lalithbuilds/episodai)。

**Episoda Core MCP** 是一个针对 AI 编程智能体（Claude Code, Cursor, Windsurf 等）的超轻量、极速本地持久化记忆层。它完全基于 Python 3 标准库（`sqlite3`、`json`、`hashlib`）构建。

### 🌟 核心特性
1. **纯标准库（0 依赖）：** 无需 `pip install`，无复杂虚拟环境，无 Docker 容器负担。
2. **艾宾浩斯自动遗忘曲线：** 自动淡化陈旧未访问的历史上下文，访问频次高的规则永久固化。
3. **SQLite WAL 高并发与自动备份：** Cursor 与 Claude Code 同时读写无锁冲突。
4. **内置交互式终端 CLI 与 TUI：** 随时通过 `python3 episoda.py tui` 或命令行直观管理记忆。

---

## ⚡ 快速配置 (Claude Code / Cursor)

在 Cursor 的 `~/.cursor/mcp.json` 中配置：
```json
{
  "mcpServers": {
    "episoda": {
      "command": "python3",
      "args": ["server.py"],
      "cwd": "/你的本地路径/episoda-core-mcp"
    }
  }
}
```

或者使用 Claude Code CLI 一键挂载：
```bash
claude mcp add episoda python3 /你的本地路径/episoda-core-mcp/server.py
```

---

## 📜 开源协议
MIT 开源许可证 · 由 [Lalith Alpuri](https://github.com/lalithbuilds) 研发构建。

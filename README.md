<div align="center">

# 🐦‍⬛ murder

**An agentic dev harness — a murder of crows for your codebase.**

[![python](https://img.shields.io/badge/python-≥3.10-blue?logo=python&logoColor=white)](pyproject.toml)
[![status](https://img.shields.io/badge/status-WIP-orange)](README.md)
[![license](https://img.shields.io/badge/license-MIT-lightgrey)](LICENSE)

</div>

> [!NOTE]
> Placeholder readme. Everything below is in flux.

> *the crows have notes.*

<details open>
<summary><b>⚡ start</b></summary>

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e .
murder init && murder
```

Bare <kbd>murder</kbd> opens the TUI.

</details>

<details>
<summary><b>🔪 commands</b></summary>

| cmd | does |
| --- | --- |
| `murder` | launch the TUI |
| `murder down` | stop the service |
| `murder down -s NAME` | stop a named service from `murder ls` |
| `murder id` | print this directory's service session id |
| `murder ls` | list running service instances |
| `murder kick` | kick off a ticket |
| `murder init` | scaffold a project |


</details>

<details>
<summary><b>🧪 dev</b></summary>

```bash
pip install -e ".[dev]"
pytest
```

→ [CONTRIBUTING.md](CONTRIBUTING.md)

</details>

---

<sub>readme subject to murder · v0.0.1</sub>

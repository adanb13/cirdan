import json

from cirdan.agents.installer import END_MARK, START_MARK, install


def test_project_install_all_platforms(tmp_path):
    written = install(project=True, root=tmp_path)
    assert set(written) == {"claude", "codex", "cursor", "gemini", "generic"}
    assert (tmp_path / ".claude" / "skills" / "cirdan" / "SKILL.md").is_file()
    assert (tmp_path / ".cursor" / "rules" / "cirdan.mdc").is_file()
    assert (tmp_path / ".agents" / "skills" / "cirdan" / "SKILL.md").is_file()
    assert "cirdan query" in (tmp_path / "CLAUDE.md").read_text()
    assert "cirdan query" in (tmp_path / "AGENTS.md").read_text()
    assert "cirdan query" in (tmp_path / "GEMINI.md").read_text()
    mcp = json.loads((tmp_path / ".mcp.json").read_text())
    assert mcp["mcpServers"]["cirdan"]["args"] == ["serve-mcp"]


def test_install_preserves_existing_content_and_is_idempotent(tmp_path):
    claude_md = tmp_path / "CLAUDE.md"
    claude_md.write_text("# My project\n\nDo not delete me.\n")
    install(platforms=["claude"], project=True, root=tmp_path)
    text = claude_md.read_text()
    assert "Do not delete me." in text
    assert text.count(START_MARK) == 1

    install(platforms=["claude"], project=True, root=tmp_path)
    text2 = claude_md.read_text()
    assert text2.count(START_MARK) == 1
    assert text2.count(END_MARK) == 1
    assert "Do not delete me." in text2


def test_mcp_json_merge_keeps_other_servers(tmp_path):
    (tmp_path / ".mcp.json").write_text(json.dumps({"mcpServers": {"other": {"command": "x"}}}))
    install(platforms=["claude"], project=True, root=tmp_path)
    data = json.loads((tmp_path / ".mcp.json").read_text())
    assert "other" in data["mcpServers"]
    assert "cirdan" in data["mcpServers"]


def test_unknown_platform_raises(tmp_path):
    import pytest

    with pytest.raises(ValueError):
        install(platforms=["nope"], project=True, root=tmp_path)


def test_user_scope_instructions_use_system_flag(tmp_path, monkeypatch):
    from pathlib import Path

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))

    install(platforms=["claude"], project=False)
    text = (home / ".claude" / "CLAUDE.md").read_text()
    assert 'cirdan query "<question>" --system' in text
    assert "cirdan map --system" in text
    assert "~/.cirdan" in text
    assert "Artifacts land in `~/.cirdan/`" in text
    skill = (home / ".claude" / "skills" / "cirdan" / "SKILL.md").read_text()
    assert "--system" in skill


def test_project_scope_instructions_unflagged(tmp_path):
    install(platforms=["claude"], project=True, root=tmp_path)
    text = (tmp_path / "CLAUDE.md").read_text()
    assert "cirdan map ." in text
    assert "--system" not in text

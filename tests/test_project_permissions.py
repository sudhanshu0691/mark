import json
from pathlib import Path
from core import permissions
from actions import fs_full


def test_project_source_edits_require_opt_in_and_keep_private_files_blocked(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, 'home', classmethod(lambda cls, home=tmp_path: home))
    tmp_path = tmp_path / 'Downloads'
    tmp_path.mkdir()
    monkeypatch.setattr(permissions, 'BASE_DIR', tmp_path)
    policy = tmp_path / 'permissions.json'
    monkeypatch.setattr(permissions, 'PERM_FILE', policy)
    project_file = tmp_path / 'main.py'
    policy.write_text(json.dumps({'allow_project_edits': False}), encoding='utf-8')
    assert not permissions.check(project_file, write=True)[0]
    policy.write_text(json.dumps({'allow_project_edits': True}), encoding='utf-8')
    result = fs_full._write(project_file, 'print(42)\n', False)
    assert result.status == 'DONE' and project_file.read_text() == 'print(42)\n'
    for path in (tmp_path / '.env', tmp_path / '.git' / 'config',
                 tmp_path / 'config' / 'api_keys.json', tmp_path / 'memory' / 'identity' / 'person.jpg',
                 tmp_path / 'memory' / 'private.json', tmp_path / '.venv' / 'script.py'):
        assert not permissions.check(path, write=True)[0]


def test_project_edits_still_obey_allowed_roots(tmp_path, monkeypatch):
    monkeypatch.setattr(permissions, 'BASE_DIR', tmp_path)
    policy = tmp_path / 'permissions.json'
    monkeypatch.setattr(permissions, 'PERM_FILE', policy)
    policy.write_text(json.dumps({'allow_project_edits': True, 'allowed_roots': [str(tmp_path / 'other')]}), encoding='utf-8')
    assert not permissions.check(tmp_path / 'main.py', write=True)[0]

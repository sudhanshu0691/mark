import sys
import types

import main


def test_livekit_environment_is_loaded_without_ini(monkeypatch, tmp_path):
    monkeypatch.setattr(main, 'BASE_DIR', tmp_path)
    for name in ('LIVEKIT_URL', 'LIVEKIT_API_KEY', 'LIVEKIT_API_SECRET', 'LIVEKIT_ROOM', 'LIVEKIT_PARTICIPANT'):
        monkeypatch.delenv(name, raising=False)
    config = tmp_path / 'config'
    config.mkdir()
    (config / 'runtime.env').write_text('LIVEKIT_URL=wss://example.test\nLIVEKIT_API_KEY=key\nLIVEKIT_API_SECRET=secret\n', encoding='utf-8')
    assert main._load_livekit_config() == {
        'url': 'wss://example.test', 'api_key': 'key', 'api_secret': 'secret',
        'room_name': 'mark-room', 'participant_name': 'mark-user',
    }


def test_livekit_token_has_room_grant(monkeypatch):
    import pytest
    api = pytest.importorskip('livekit.api')
    import jwt
    monkeypatch.setattr(main, '_load_livekit_config', lambda: {'api_key': 'key', 'api_secret': 'a' * 32})
    token = main._generate_livekit_token('room', 'participant')
    claims = jwt.decode(token, 'a' * 32, algorithms=['HS256'])
    assert claims['sub'] == 'participant'
    assert claims['video']['roomJoin'] is True
    assert claims['video']['room'] == 'room'

"""核對合併後部署設定；不輸出 Compose secrets，不啟動服務。"""
import json
import os
from pathlib import Path
import subprocess


def validate(config, *, port, repository):
    services = config['services']
    expected = {'cloudflared': {'edge'}, 'frontend': {'edge', 'application'},
                'backend': {'application', 'database'}, 'postgres': {'database'},
                'model-bridge': {'application'}}
    for name, networks in expected.items():
        service = services[name]
        assert set(service['networks']) == networks, name
        assert not service.get('privileged'), name
        assert service.get('network_mode') != 'host', name
        assert not any('docker.sock' in v.get('source', '') for v in service.get('volumes', [])), name
        if name != 'frontend': assert not service.get('ports'), name
    ports = services['frontend']['ports']
    assert len(ports) == 1
    assert (ports[0]['host_ip'], str(ports[0]['published']), ports[0]['target']) == ('127.0.0.1', str(port), 8080)
    assert config['networks']['database']['internal'] is True
    connector = services['cloudflared']
    assert connector['read_only'] and connector['restart'] == 'unless-stopped'
    assert connector['cap_drop'] == ['ALL']
    assert not connector.get('environment')
    assert '--token' not in connector['command'] and '--token-file' in connector['command']
    assert '@sha256:' in connector['image'] and ':latest' not in connector['image']
    mounts = connector['volumes']
    assert len(mounts) == 1 and mounts[0]['read_only']
    source = Path(mounts[0]['source'])
    assert source.is_absolute() and not source.resolve().is_relative_to(repository.resolve())
    for name, service in services.items():
        if name != 'cloudflared':
            assert not any(v.get('source') == str(source) for v in service.get('volumes', [])), name
    backend = services['backend']['environment']
    assert backend['STUDYDY_PUBLIC_ORIGIN'] == 'https://studydy.net'
    assert str(backend['STUDYDY_SECURE_COOKIE']).lower() == 'true'
    assert str(backend['STUDYDY_UPLOAD_MAX_BYTES']) == str(services['frontend']['environment']['STUDYDY_UPLOAD_MAX_BYTES'])


if __name__ == '__main__':
    # 必須傳明確 env-file；禁止隱含讀取正式 .env。
    env_file = os.environ['STUDYDY_TEST_ENV_FILE']
    result = subprocess.run(['docker','compose','--env-file',env_file,'--profile','tunnel','--profile','ssh',
        '-f','compose.yaml','-f','compose.tunnel.yaml','config','--format','json'], capture_output=True, text=True)
    assert result.returncode == 0, 'Compose configuration invalid (output withheld)'
    validate(json.loads(result.stdout), port=int(os.environ.get('STUDYDY_TEST_PORT', '4173')), repository=Path.cwd())
    print('PASS: merged deployment ports, networks, token mount isolation, HTTPS settings')

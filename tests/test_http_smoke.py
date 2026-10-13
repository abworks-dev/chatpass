import asyncio
import socket
import threading

import httpx
import uvicorn
from test_storage import MemoryKeyring

from chatpass.config import Settings
from chatpass.gateway import create_app
from chatpass.storage import StateStore


def test_real_listener_auth_models_and_stream(tmp_path):
    store = StateStore(tmp_path, backend=MemoryKeyring())
    key = store.rotate_gateway_key()
    class Credentials:
        async def get_access_token(self):
            return 'mock-upstream-access'
    def upstream(request):
        assert request.headers['authorization'] == 'Bearer mock-upstream-access'
        if request.url.path == '/v1/models':
            return httpx.Response(200, json={'models': [{'slug': 'mock-model', 'display_name': 'Mock', 'visibility': 'list'}]})
        assert request.url.path == '/v1/responses'
        return httpx.Response(200, headers={'content-type': 'text/event-stream'}, content=(
            b'event: response.output_text.delta\ndata: {"type":"response.output_text.delta","delta":"hello"}\n\n'
            b'event: response.completed\ndata: {"type":"response.completed","response":{"status":"completed"}}\n\n'))
    started = threading.Event()
    class Server(uvicorn.Server):
        async def startup(self, sockets=None):
            await super().startup(sockets=sockets)
            started.set()
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        listener.listen(128)
        port = listener.getsockname()[1]
        injected = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
        app = create_app(Settings(data_dir=tmp_path), Credentials(), store.verify_gateway_key, client=injected)
        server = Server(uvicorn.Config(app, log_level='critical', access_log=False))
        thread = threading.Thread(target=lambda: server.run(sockets=[listener]), daemon=True)
        thread.start()
        try:
            assert started.wait(5)
            with httpx.Client(base_url=f'http://127.0.0.1:{port}', timeout=5, trust_env=False) as client:
                assert client.get('/health').json() == {'status': 'ok'}
                assert client.get('/v1/models').status_code == 401
                headers = {'Authorization': 'Bearer ' + key}
                assert client.get('/v1/models', headers=headers).json()['data'][0]['id'] == 'mock-model'
                with client.stream('POST', '/v1/responses', headers=headers,
                        json={'model': 'mock-model', 'input': 'test', 'stream': True}) as response:
                    assert response.status_code == 200
                    text = ''.join(response.iter_text())
                    assert 'hello' in text and 'response.completed' in text
                new_key = store.rotate_gateway_key()
                assert client.get('/v1/models', headers=headers).status_code == 401
                assert client.get('/v1/models', headers={'Authorization': 'Bearer '+new_key}).status_code == 200
        finally:
            server.should_exit = True
            thread.join(5)
            assert not thread.is_alive()
            asyncio.run(injected.aclose())

from types import SimpleNamespace
import json
import pytest
from esgenie import llm, llm_cache
from esgenie.openai_client import connection_identity, configured_connection, normalized_endpoint


@pytest.fixture
def client_factory(monkeypatch):
    calls = []
    monkeypatch.setattr(llm.SETTINGS, 'force_mock', False)
    monkeypatch.setenv('ESGENIE_FORCE_MOCK', '0')
    monkeypatch.setattr(llm.SETTINGS, 'openai_api_key', 'private-synthetic-key')
    monkeypatch.setattr(llm.SETTINGS, 'azure_openai_endpoint', 'https://a.invalid/openai/v1')
    monkeypatch.setattr(llm.SETTINGS, 'strict_llm', True)
    def factory(settings):
        address = settings.azure_openai_endpoint
        def create(**kwargs):
            calls.append(address)
            return SimpleNamespace(model='returned-deployment', id='resp-1', _request_id='req-1',
                                   usage={'total_tokens': 10}, choices=[SimpleNamespace(message=SimpleNamespace(content=address))])
        return SimpleNamespace(base_url=address, chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    monkeypatch.setattr(llm, 'create_openai_client', factory)
    return calls


def test_existing_client_ignores_changed_global_endpoint(client_factory, monkeypatch):
    client = llm.LLMClient()
    first = client.complete('s','u')
    monkeypatch.setattr(llm.SETTINGS, 'azure_openai_endpoint', 'https://b.invalid/openai/v1')
    second = client.complete('s','u')
    assert first.content == second.content and len(client_factory) == 1
    third = llm.LLMClient().complete('s','u')
    assert third.content != first.content and len(client_factory) == 2


def test_equivalent_url_reuses_cache_and_metadata(client_factory, monkeypatch):
    first = llm.LLMClient().complete('s','u')
    monkeypatch.setattr(llm.SETTINGS, 'azure_openai_endpoint', 'https://A.invalid:443/openai/v1/')
    second = llm.LLMClient().complete('s','u')
    assert second.meta['cache'] == 'hit' and len(client_factory) == 1
    assert first.meta['returned_model'] == second.meta['returned_model'] == 'returned-deployment'
    assert second.meta['request_id'] == 'req-1'


def test_unused_settings_do_not_partition_openai_cache(client_factory, monkeypatch):
    llm.LLMClient().complete('s','u')
    monkeypatch.setenv('ANTHROPIC_BASE_URL', 'https://unrelated.invalid')
    monkeypatch.setattr(llm.SETTINGS, 'azure_api_version', 'unused-version-on-v1')
    assert llm.LLMClient().complete('s','u').meta['cache'] == 'hit'
    assert len(client_factory) == 1


@pytest.mark.parametrize('provider', ['openai','anthropic'])
def test_real_client_api_version_and_path_are_in_identity(provider):
    fallback = configured_connection(llm.SETTINGS, provider)
    def identity(path, version):
        client = SimpleNamespace(base_url='https://a.invalid/'+path,
                                 _api_version=version, default_headers={'anthropic-version': version})
        return connection_identity(client, provider, fallback)
    assert identity('v1','1') != identity('v2','1')
    assert identity('v1','1') != identity('v1','2')
    assert identity('v1','1') == identity('v1/','1')


def test_no_key_or_token_in_cache_identity_or_metadata(client_factory):
    llm.LLMClient().complete('s','u')
    contents = '\n'.join(p.read_text() for p in llm_cache.cache_dir().glob('*.json'))
    assert 'private-synthetic-key' not in contents
    assert normalized_endpoint('https://user:secret@a.invalid/v1?api-key=private-synthetic-key') == 'https://a.invalid/v1'


def test_old_addressless_cache_is_preserved_and_not_reused(client_factory):
    directory = llm_cache.cache_dir(); directory.mkdir(parents=True, exist_ok=True)
    old = directory / 'old.json'; old.write_text(json.dumps({'schema': 1, 'content': 'old'}))
    assert llm.LLMClient().complete('s','u').meta['cache'] == 'miss'
    assert old.exists() and len(client_factory) == 1


def test_outer_ocr_cache_is_also_connection_scoped():
    from esgenie.ssot.ocr_cache import make_key
    args = dict(model='gpt-4.1-mini',prompt='extract',doc_type='policy',llm_input='source')
    a = make_key(**args,connection={'provider':'openai','endpoint_sha256':'A'})
    b = make_key(**args,connection={'provider':'openai','endpoint_sha256':'B'})
    assert a != b
    assert a == make_key(**args,connection={'endpoint_sha256':'A','provider':'openai'})

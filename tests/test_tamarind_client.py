import json
import httpx
import pytest
from app.tamarind.client import TamarindClient, TamarindError


def make_client(handler):
    return TamarindClient(api_key='test-key', base_url='https://unit.test/api/', transport=httpx.MockTransport(handler))


def test_tools_discovery():
    def handler(req):
        assert req.url.path == '/api/tools'
        assert req.headers['x-api-key'] == 'test-key'
        return httpx.Response(200, json=[{'name':'alphafold','settings':[{'name':'sequence','required':True}]}])
    assert make_client(handler).tools()[0]['name'] == 'alphafold'


def test_validation_rejects_invalid_without_submit():
    calls=[]
    def handler(req):
        calls.append(req.url.path)
        return httpx.Response(200, json={'valid':False,'error':'sequence required'})
    # Validate method returns provider response; API layer decides not to submit.
    result=make_client(handler).validate('alphafold', {})
    assert result['valid'] is False
    assert calls == ['/api/validate-job']


def test_submit_payload():
    def handler(req):
        assert req.url.path == '/api/submit-job'
        payload=json.loads(req.content)
        assert payload['jobName']=='test-run'
        assert payload['type']=='alphafold'
        assert payload['settings']['sequence']=='ACDE'
        return httpx.Response(200, text='test-run submitted to queue.')
    assert 'submitted' in make_client(handler).submit('test-run','alphafold',{'sequence':'ACDE'})['message']


def test_api_errors_are_explicit():
    def handler(req): return httpx.Response(401, json={'error':'Invalid API key'})
    with pytest.raises(TamarindError, match='HTTP 401'):
        make_client(handler).tools()

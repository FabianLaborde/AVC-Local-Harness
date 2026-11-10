import io
import json
import unittest
from unittest.mock import patch

import server
from service_status import (localai_status, worker_gpu_usage, nvidia_usage,
                            localai_usage, LOCALAI_MODEL, _USAGE_CACHE)


class Response(io.BytesIO):
    def __enter__(self):return self
    def __exit__(self,*args):self.close()


class ServiceStatusTests(unittest.TestCase):
    def test_real_gpu_usage_parsers_are_bounded_and_independent(self):
        def runner(command,**kwargs):
            value='37\n' if command[0]=='nvidia-smi.exe' else '12,6\n'
            return type('Result',(),{'returncode':0,'stdout':value})()
        self.assertEqual(nvidia_usage(runner),37)
        self.assertEqual(localai_usage(runner),13)
        def failed(command,**kwargs):raise OSError('sin contador')
        self.assertIsNone(nvidia_usage(failed));self.assertIsNone(localai_usage(failed))

    def test_gpu_usage_cache_avoids_repeated_native_probes(self):
        calls=[]
        def runner(command,**kwargs):
            calls.append(command[0]);return type('Result',(),{'returncode':0,'stdout':'4'})()
        _USAGE_CACHE.update(at=-1.0,value={'ollama':None,'localai':None})
        self.assertEqual(worker_gpu_usage(False,runner,lambda:100),{'ollama':4,'localai':4})
        self.assertEqual(worker_gpu_usage(False,runner,lambda:105),{'ollama':4,'localai':4})
        self.assertEqual(len(calls),2)
        worker_gpu_usage(True,runner,lambda:105)
        self.assertEqual(len(calls),4)

    def test_localai_distinguishes_service_from_configured_model(self):
        def available(url,timeout):
            return Response(json.dumps({'data':[{'id':LOCALAI_MODEL}]}).encode())
        self.assertEqual(localai_status(available),
            {'ok':True,'model':LOCALAI_MODEL,'model_ready':True})
        def other_model(url,timeout):
            return Response(json.dumps({'data':[{'id':'otro'}]}).encode())
        result=localai_status(other_model)
        self.assertTrue(result['ok']);self.assertFalse(result['model_ready'])
        def unavailable(url,timeout):raise OSError('sin conexión')
        result=localai_status(unavailable)
        self.assertFalse(result['ok']);self.assertIn('sin conexión',result['error'])

    def test_status_endpoint_keeps_ollama_and_localai_independent(self):
        ollama=Response(json.dumps({'models':[{'name':server.CODE_MODEL},
            {'name':server.VISION_MODEL}]}).encode())
        local={'ok':True,'model':LOCALAI_MODEL,'model_ready':True}
        with patch('server.urllib.request.urlopen',return_value=ollama), \
                patch('server.localai_status',return_value=local), \
                patch('server.worker_gpu_usage',return_value={'ollama':27,'localai':9}):
            response=server.app.test_client().get('/api/status')
        data=response.get_json();self.assertTrue(data['ok']);self.assertEqual(data['usage_percent'],27)
        self.assertEqual(data['localai']['usage_percent'],9)
        self.assertTrue(data['code_ready']);self.assertTrue(data['vision_ready'])
        self.assertIn('no-store',response.headers['Cache-Control'])

    def test_web_has_separate_localai_indicator_and_refresh(self):
        page=(server.ROOT/'web/index.html').read_text(encoding='utf8')
        self.assertIn('id="localaiStatus"',page)
        self.assertIn("setInterval(refreshStatus,3000)",page)
        self.assertIn("usageLabel(d.usage_percent)",page)
        self.assertIn("'No conectado'",page)


if __name__=='__main__':unittest.main()

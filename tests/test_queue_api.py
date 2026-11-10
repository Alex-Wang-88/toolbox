import io,sys,time,threading,tempfile,unittest
from pathlib import Path
from unittest import mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import web_server as w
from task_queue import TaskQueue

class QueueApiTests(unittest.TestCase):
    def setUp(self):
        self.previous=w.task_queue
        w.task_queue=TaskQueue(w.tasks)
        self.before=set(w.tasks)
        self.release=threading.Event()
        self.client=w.app.test_client()
    def tearDown(self):
        self.release.set()
        end=time.monotonic()+3
        while w.task_queue.busy and time.monotonic()<end:time.sleep(.005)
        self.assertFalse(w.task_queue.busy)
        w.task_queue=self.previous
        for key in set(w.tasks)-self.before:w.tasks.pop(key,None)
    def gate(self):
        ready=threading.Event();tid='queue-api-gate';w.tasks[tid]={}
        def run():ready.set();self.release.wait(3)
        w.enqueue_task(tid,run,kind='test',title='gate',page='/tts');self.assertTrue(ready.wait(1))
    def test_different_feature_requests_queue_without_mode_switching_then_cancel(self):
        self.gate()
        with mock.patch.object(w,'imagegen_check_ready',return_value=(True,'')),mock.patch.object(w.mode_manager,'ensure') as mode:
            r=self.client.post('/api/imagegen/generate',json={'prompt':'test'})
        self.assertEqual(r.status_code,202);mode.assert_not_called();tid=r.json['task_id']
        q=self.client.get('/api/queue').json
        self.assertEqual(q['waiting_count'],1);self.assertEqual(w.tasks[tid]['status'],'pending')
        self.assertEqual(self.client.post('/api/cancel/'+tid).status_code,200)
        self.assertEqual(w.tasks[tid]['status'],'cancelled')
        self.assertEqual(self.client.get('/api/queue').json['waiting_count'],0)
    def test_multipart_upload_survives_request_close_and_executes_later(self):
        self.gate()
        with mock.patch.object(w,'transcribe_audio',return_value={'text':'hello','language':'en'}),mock.patch.object(w.VOICE_VALIDATION,'validate_file',return_value={'ok':True,'duration_sec':5}),mock.patch.object(w.VOICE_VALIDATION,'prepare_clone_ref',return_value={'ok':True}):
            r=self.client.post('/api/voices/transcribe',data={'async':'true','file':(io.BytesIO(b'RIFFsample'),'test.wav')},content_type='multipart/form-data')
            self.assertEqual(r.status_code,202);tid=r.json['task_id'];r.close()
            self.assertEqual(w.tasks[tid]['status'],'pending');self.release.set()
            end=time.monotonic()+2
            while w.tasks[tid]['status'] in ['pending','processing'] and time.monotonic()<end:time.sleep(.005)
            self.assertEqual(w.tasks[tid]['status'],'completed',w.tasks[tid]);self.assertEqual(w.tasks[tid]['result']['text'],'hello')
    def test_cancel_queued_spooled_request_and_legacy_error_response(self):
        self.gate()
        r=self.client.post('/api/voices/create',data={'async':'true','name':'test'})
        self.assertEqual(r.status_code,202);tid=r.json['task_id']
        self.assertEqual(self.client.post('/api/cancel/'+tid).status_code,200)
        self.release.set()
        r=self.client.post('/api/voices/create',data={'name':'test'})
        self.assertEqual(r.status_code,400);self.assertFalse(r.json['ok'])

if __name__=='__main__':unittest.main()

import sys, os, time, threading, unittest, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'src'))
from task_queue import TaskQueue

class QueueTests(unittest.TestCase):
    def setUp(self):
        self.tasks={}; self.queue=TaskQueue(self.tasks)
    def submit(self, key, fn, **kw):
        self.tasks[key]={}
        self.queue.submit(key, fn, kind='test', title=key, page='/tts', **kw)
    def idle(self):
        end=time.monotonic()+3
        while self.queue.busy and time.monotonic()<end: time.sleep(.005)
        self.assertFalse(self.queue.busy)
    def test_empty_starts_immediately_and_fifo_never_overlaps(self):
        started=threading.Event(); release=threading.Event(); order=[]
        def first():
            order.append('start-1'); started.set(); release.wait(2); order.append('end-1')
        self.submit('1',first); self.assertTrue(started.wait(1))
        self.submit('2',lambda:order.append('2')); self.submit('3',lambda:order.append('3'))
        snapshot=self.queue.snapshot()
        self.assertEqual(snapshot['waiting_count'],2)
        self.assertEqual(self.tasks['2']['queue_position'],1)
        self.assertEqual(self.tasks['3']['queue_position'],2)
        release.set(); self.idle(); self.assertEqual(order,['start-1','end-1','2','3'])
    def test_pending_cancel_releases_upload_and_reindexes(self):
        release=threading.Event(); started=threading.Event(); order=[]; cleaned=[]
        def first(): started.set(); release.wait(2)
        self.submit('1',first); started.wait(1)
        self.submit('2',lambda:order.append('2'),cleanup=lambda:cleaned.append('2'))
        self.submit('3',lambda:order.append('3'))
        self.assertTrue(self.queue.cancel('2')); self.assertEqual(cleaned,['2'])
        self.assertEqual(self.tasks['2']['status'],'cancelled'); self.assertEqual(self.tasks['3']['queue_position'],1)
        release.set(); self.idle(); self.assertEqual(order,['3'])
    def test_active_cancel_does_not_release_lane_until_worker_returns(self):
        started=threading.Event(); release=threading.Event(); ran=threading.Event()
        def first(): started.set(); release.wait(2)
        self.submit('1',first); started.wait(1); self.submit('2',ran.set)
        self.assertTrue(self.queue.cancel('1')); self.assertFalse(ran.wait(.05))
        release.set(); self.idle(); self.assertTrue(ran.is_set()); self.assertEqual(self.tasks['1']['status'],'cancelled')
    def test_failure_and_preparation_failure_advance_queue(self):
        started=threading.Event(); release=threading.Event(); order=[]
        def first(): started.set(); release.wait(2); raise ValueError('broken')
        def fail_prepare(): raise RuntimeError('prepare failure')
        self.submit('1',first); started.wait(1)
        self.submit('2',lambda:order.append('bad'),prepare=fail_prepare)
        self.submit('3',lambda:order.append('3')); release.set(); self.idle()
        self.assertEqual(order,['3']); self.assertEqual(self.tasks['1']['status'],'failed'); self.assertEqual(self.tasks['2']['status'],'failed')
    def test_concurrent_submissions_have_no_dropped_tasks(self):
        lock=threading.Lock(); order=[]
        def run(k):
            with lock: order.append(k)
        threads=[threading.Thread(target=self.submit,args=(str(i),lambda i=i:run(i))) for i in range(24)]
        for t in threads:t.start()
        for t in threads:t.join()
        self.idle(); self.assertEqual(sorted(order),list(range(24)))
    def test_snapshot_filters_internal_fields(self):
        self.submit('1',lambda:None); self.idle();self.tasks['1']['_secret']='private'
        self.assertNotIn('_secret',self.queue.snapshot()['tasks'][0])

if __name__=='__main__': unittest.main()

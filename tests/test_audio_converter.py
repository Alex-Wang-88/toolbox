import io, math, struct, sys, tempfile, time, unittest, wave, subprocess
from pathlib import Path
from unittest import mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import audio_converter as ac
from test_ncm_converter import _make_ncm

class AudioConversionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if len(ac.status()['output_formats']) != 7: raise unittest.SkipTest('需要本地 FFmpeg 七种输出编码器')
        cls.temp = tempfile.TemporaryDirectory();cls.root=Path(cls.temp.name)
        cls.source=cls.root/'真实音频_含中文.wav'
        with wave.open(str(cls.source),'wb') as w:
            w.setparams((1,2,44100,0,'NONE','not compressed'))
            w.writeframes(b''.join(struct.pack('<h',int(math.sin(2*math.pi*220*i/44100)*9000)) for i in range(44100)))
    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()
    def test_seven_formats_are_really_written_and_can_convert_to_each_other(self):
        expected={'wav':'pcm_s16le','mp3':'mp3','flac':'flac','m4a':'aac','aac':'aac','ogg':'vorbis','opus':'opus'}
        inputs={}
        for fmt in expected:
            target=self.root/('encoded.'+fmt);result=ac.convert_audio(str(self.source),str(target),fmt)
            self.assertEqual(result['output_codec'],expected[fmt]);inputs[fmt]=target
        for source_fmt,path in inputs.items():
            for target_fmt,codec in expected.items():
                with self.subTest(source=source_fmt,target=target_fmt):
                    target=self.root/f'{source_fmt}-to-{target_fmt}.{target_fmt}'
                    converted=ac.convert_audio(str(path),str(target),target_fmt)
                    self.assertEqual(converted['source_format'],source_fmt)
                    self.assertEqual(converted['output_codec'],codec)
                    self.assertGreater(converted['duration_sec'],.9)
                    self.assertFalse(list(self.root.glob('.audio_*.part')))
    def test_content_detection_ignores_wrong_extension_including_ncm(self):
        renamed=self.root/'actually-wav.ncm';renamed.write_bytes(self.source.read_bytes())
        self.assertEqual(ac.inspect_audio(str(renamed))['source_format'],'wav')
        ncm=self.root/'actually-ncm.mp3';_make_ncm(str(ncm),self.source.read_bytes(),source_format='wav')
        self.assertEqual(ac.inspect_audio(str(ncm))['source_format'],'ncm')
        result=ac.convert_audio(str(ncm),str(self.root/'decoded.opus'),'opus')
        self.assertEqual(result['source_format'],'ncm');self.assertEqual(result['output_codec'],'opus')
    def test_preserves_24bit_pcm_and_does_not_force_resampling_for_lossless(self):
        source=self.root/'pcm24.wav'
        with wave.open(str(source),'wb') as w:
            w.setparams((1,3,48000,0,'NONE','not compressed'));w.writeframes(b'\x00\x80\x02'*48000)
        for fmt in ['wav','flac']:
            target=self.root/f'preserved.{fmt}';ac.convert_audio(str(source),str(target),fmt)
            info=ac.probe_audio(str(target));self.assertEqual(info['bits_per_sample'],24);self.assertEqual(info['sample_rate'],48000)
    def test_plain_audio_stays_ready_without_ncm_crypto_dependency(self):
        with mock.patch.object(ac,'ncm_status',return_value={'ready':False,'reason':'missing crypto'}):
            ready=ac.status();self.assertTrue(ready['ready']);self.assertFalse(ready['ncm']['ready'])
    def test_invalid_audio_and_unsupported_output_are_rejected(self):
        bad=self.root/'bad.mp3';bad.write_bytes(b'not audio')
        with self.assertRaises(ac.AudioConversionError):ac.convert_audio(str(bad),str(self.root/'invalid.wav'))
        with self.assertRaises(ac.AudioConversionError):ac.convert_audio(str(self.source),str(self.root/'nope.exe'),'exe')
    def test_timeout_leaves_existing_output_unchanged_and_cleans_partial(self):
        target=self.root/'keep.wav';target.write_bytes(b'previous output')
        info={'source_format':'wav','codec':'pcm_s16le','bits_per_sample':16}
        with mock.patch.object(ac,'probe_audio',return_value=info),mock.patch.object(ac.subprocess,'run',side_effect=subprocess.TimeoutExpired(['ffmpeg'],1)):
            with self.assertRaises(ac.AudioConversionError):ac.convert_audio(str(self.source),str(target))
        self.assertEqual(target.read_bytes(),b'previous output');self.assertFalse(list(self.root.glob('.audio_*.part')))
    def test_cancelling_ncm_decode_does_not_publish_an_output(self):
        source=self.root/'cancel-input.ncm';_make_ncm(str(source),self.source.read_bytes(),source_format='wav')
        target=self.root/'cancelled.flac'
        def cancel(progress, message):
            if progress>10:raise RuntimeError('cancel requested')
        with self.assertRaisesRegex(RuntimeError,'cancel requested'):
            ac.convert_audio(str(source),str(target),'flac',progress_callback=cancel)
        self.assertFalse(target.exists());self.assertFalse(list(self.root.glob('.audio_*.part')))

class AudioConverterApiTests(unittest.TestCase):
    def test_mixed_batch_partial_failure_deduplication_and_downloads(self):
        import web_server as w
        from task_queue import TaskQueue
        temp=tempfile.TemporaryDirectory();root=Path(temp.name)
        buff=io.BytesIO()
        with wave.open(buff,'wb') as wav:
            wav.setparams((1,2,44100,0,'NONE','not compressed'));wav.writeframes(b'\x00\x20'*44100)
        before=set(w.tasks);previous=w.task_queue;w.task_queue=TaskQueue(w.tasks)
        try:
            with mock.patch.object(w,'get_output_folder',return_value=str(root)):
                client=w.app.test_client()
                response=client.post('/api/audio-converter/convert',data={'format':'flac','consent':'true','files':[(io.BytesIO(buff.getvalue()),'同名.mp3'),(io.BytesIO(b'broken'),'bad.wav'),(io.BytesIO(buff.getvalue()),'同名.wav')]},content_type='multipart/form-data')
                self.assertEqual(response.status_code,202,response.json);tid=response.json['task_id'];response.close()
                end=time.monotonic()+10
                while w.task_queue.busy and time.monotonic()<end:time.sleep(.01)
                self.assertFalse(w.task_queue.busy)
                task=client.get('/api/status/'+tid).json;self.assertEqual(task['status'],'completed',task)
                result=task['result'];self.assertEqual((result['success_count'],result['failed_count']),(2,1))
                self.assertEqual([item['filename'] for item in result['items'] if item['status']=='completed'],['同名.flac','同名 (2).flac'])
                self.assertEqual(result['items'][0]['source_format'],'wav')
                download=client.get(result['items'][0]['download_url']);self.assertEqual(download.status_code,200);self.assertEqual(download.mimetype,'audio/flac');self.assertTrue(download.data.startswith(b'fLaC'));download.close()
                archive=client.get(result['zip_url']);self.assertEqual(archive.status_code,200);self.assertTrue(archive.data.startswith(b'PK'));archive.close()
                self.assertEqual(client.get(result['items'][0]['audio_url'],headers={'Range':'bytes=0-3'}).status_code,206)
        finally:
            w.task_queue=previous
            for tid in set(w.tasks)-before:w.tasks.pop(tid,None)
            temp.cleanup()
    def test_api_rejects_invalid_format_and_requires_consent(self):
        import web_server as w
        client=w.app.test_client()
        self.assertEqual(client.post('/api/audio-converter/convert',data={'consent':'true','format':'exe'}).status_code,400)
        self.assertEqual(client.post('/api/audio-converter/convert',data={'format':'flac'}).status_code,400)

if __name__=='__main__':unittest.main()

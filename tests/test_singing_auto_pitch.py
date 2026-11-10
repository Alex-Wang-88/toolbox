import sys, math, unittest, tempfile
from pathlib import Path
from unittest import mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import singing_voice

class PitchRuleTests(unittest.TestCase):
    def profile(self,mid,lo=150,hi=250):return {'source':{'p50':mid},'reference':{'p10':lo,'p90':hi}}
    def test_in_range_unchanged(self):
        for f in [150,180,250]: self.assertEqual(singing_voice.suggest_pitch_shift(self.profile(f))[0],0)
    def test_extremes_capped_in_both_directions(self):
        self.assertEqual(singing_voice.suggest_pitch_shift(self.profile(600))[0],-5)
        self.assertEqual(singing_voice.suggest_pitch_shift(self.profile(60))[0],5)
    def test_outside_range_moves_toward_nearest_boundary(self):
        self.assertEqual(singing_voice.suggest_pitch_shift(self.profile(300))[0],-3)
        self.assertEqual(singing_voice.suggest_pitch_shift(self.profile(120))[0],4)
    def test_invalid_profiles_are_safe(self):
        for p in [{},self.profile(0),self.profile(float('nan')),self.profile(float('inf')),self.profile(200,250,150)]:
            self.assertEqual(singing_voice.suggest_pitch_shift(p),(0,''))
    def test_worker_applies_same_shift_to_accompaniment_and_vocal_and_fallback(self):
        import web_server as w
        for original,auto,manual,ok,expected in [(True,True,0,True,-5),(True,False,3,True,3),(False,True,0,False,0),(False,True,0,True,-5)]:
            with self.subTest(original=original,auto=auto,manual=manual,ok=ok),tempfile.TemporaryDirectory() as tmp:
                source_dir=Path(tmp,'uploads');source_dir.mkdir();source=source_dir/'source.wav';source.write_bytes(b'wav')
                ref=Path(tmp,'ref.wav');ref.write_bytes(b'ref');vocals=Path(tmp,'vocals.wav');vocals.write_bytes(b'v');acc=Path(tmp,'acc.wav');acc.write_bytes(b'a')
                out=Path(tmp,'converted.wav');out.write_bytes(b'out');tid='pitch-test';w.tasks[tid]={'status':'pending','voice_name':'Test'}
                try:
                    with mock.patch.object(w,'get_output_folder',return_value=tmp),mock.patch.object(w.gpu_arbiter,'acquire',return_value=True),mock.patch.object(w.gpu_arbiter,'release'),mock.patch.object(w,'separate_song',return_value={'vocals_path':str(vocals),'accompaniment_path':str(acc)}),mock.patch.object(w,'probe_pitch_profile',return_value={'ok':ok,**self.profile(600)}) as probe,mock.patch.object(w,'run_conversion',return_value={'path':str(out),'duration_sec':4}) as convert,mock.patch.object(w,'shift_pitch',return_value={'path':str(acc)}) as shift,mock.patch.object(w,'mix_audio',return_value={'path':str(out),'duration_sec':4}):
                        w._singing_generate_worker(tid,str(source_dir),str(source),str(ref),'voice','source.wav','test','original' if original else 'vocal',manual,20,False,auto)
                    self.assertEqual(w.tasks[tid]['status'],'completed')
                    self.assertEqual(convert.call_args.kwargs['semi_tone_shift'],expected)
                    self.assertEqual(w.tasks[tid]['result']['pitch_shift_applied'],expected)
                    if original and expected:self.assertEqual(shift.call_args.args[1],expected)
                    else:shift.assert_not_called()
                    if not auto:probe.assert_not_called()
                finally:w.tasks.pop(tid,None)

if __name__=='__main__':unittest.main()

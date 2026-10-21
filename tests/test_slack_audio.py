import json
import os
import stat
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from capo.conversation import ConversationPending
from capo.slack_audio import AudioError, audio_files, context, heard, slack_transcript, transcribe
from capo.store import Store

CONFIG = {'team_id': 'T1', 'channel_id': 'C1', 'owner_user_id': 'UOWNER'}
VOICE = {'id': 'FVOICE1', 'subtype': 'slack_audio', 'mimetype': 'audio/mp4', 'file_access': 'check_file_info'}


def body(files, user='UOWNER', text=''):
    return {'team_id': 'T1', 'event_id': 'Ev1',
            'event': {'type': 'message', 'channel': 'C1', 'user': user, 'ts': '100.1', 'text': text, 'files': files}}


def script(path, source):
    path.write_text('#!' + sys.executable + '\n' + source)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


class TranscribeTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        bin = self.dir / 'bin'; bin.mkdir()
        # Stand-ins: ffmpeg copies its input; whisper writes the text given in the environment.
        self.tools = {
            'ffmpeg': script(bin / 'ffmpeg', 'import sys,shutil\nshutil.copy(sys.argv[sys.argv.index("-i")+1], sys.argv[-1])\n'),
            'whisper_cli': script(bin / 'whisper', 'import os,sys\nopen(sys.argv[sys.argv.index("-of")+1]+".txt","w").write(os.environ.get("FAKE_SPEECH",""))\n'),
            'model': str(self.dir / 'model.bin')}
        Path(self.tools['model']).write_bytes(b'model')
        (self.dir / 'clip').write_bytes(b'audio')

    def test_local_transcription_returns_normalized_text_and_removes_the_wav(self):
        with patch.dict(os.environ, {'FAKE_SPEECH': '  Remind me to water\n the plants. '}):
            self.assertEqual(transcribe(self.tools, self.dir / 'clip', self.dir), 'Remind me to water the plants.')
        self.assertFalse((self.dir / 'audio.wav').exists())

    def test_silence_and_missing_setup_are_explained(self):
        with patch.dict(os.environ, {'FAKE_SPEECH': '[BLANK_AUDIO]'}), self.assertRaisesRegex(AudioError, 'hear any speech'):
            transcribe(self.tools, self.dir / 'clip', self.dir)
        with self.assertRaisesRegex(AudioError, 'isn’t set up'):
            transcribe(dict(self.tools, model=str(self.dir / 'absent.bin')), self.dir / 'clip', self.dir)


class VoiceContextTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name)
        self.store = Store(self.home); self.addCleanup(self.store.db.close)
        self.client = Mock()
        self.service = SimpleNamespace(store=self.store, config=CONFIG, client=self.client)

    def finish(self, event_body):
        self.store.enqueue_slack('Ev1', event_body)
        for _ in range(200):
            try:
                return context(self.service, 'Ev1', event_body, event_body['event']['text'])
            except ConversationPending:
                time.sleep(0.01)
        self.fail('Transcription did not finish')

    def test_slack_transcript_is_used_and_becomes_the_owner_request(self):
        self.client.files_info.return_value = {'file': {'size': 1000, 'duration_ms': 5000, 'transcription':
            {'status': 'complete', 'preview': {'content': 'Book the dentist for Friday', 'has_more': False}}}}
        request = self.finish(body([VOICE]))
        self.assertIn('Owner voice message (automatic transcript', request)
        self.assertTrue(request.endswith('Book the dentist for Friday'))
        self.assertEqual(heard(self.home, 'Ev1'), 'Book the dentist for Friday')

    def test_long_clips_are_refused_without_downloading(self):
        self.client.files_info.return_value = {'file': {'size': 1000, 'duration_ms': 11 * 60 * 1000}}
        with self.assertRaisesRegex(AudioError, 'under 10 minutes'):
            self.finish(body([VOICE]))
        self.assertEqual(heard(self.home, 'Ev1'), '')

    def test_only_owner_voice_messages_are_read(self):
        other = body([VOICE], user='UOTHER')
        with self.assertRaises(ValueError):
            context(self.service, 'Ev1', other, '')
        self.client.files_info.assert_not_called()
        self.assertEqual(context(self.service, 'Ev1', body([]), 'typed'), 'typed')

    def test_detection_and_partial_slack_transcripts(self):
        self.assertEqual(len(audio_files({'files': [VOICE, {'id': 'FIMG', 'mimetype': 'image/png'}]})), 1)
        self.assertEqual(slack_transcript({'transcription': {'status': 'complete', 'preview': {'content': 'x', 'has_more': True}}}), '')
        self.assertEqual(slack_transcript({'transcription': {'status': 'none'}}), '')

    def test_image_path_ignores_voice_clips(self):
        from capo.slack_images import context as image_context
        service = SimpleNamespace(store=self.store, config=CONFIG, client=self.client)
        event_body = body([VOICE])
        self.store.enqueue_slack('Ev1', event_body)
        with patch('capo.slack_thread_context.recover'):
            self.assertEqual(image_context(service, 'Ev1', event_body, 'text'), 'text')


if __name__ == '__main__':
    unittest.main()

"""Owner voice messages in Slack -> local transcript -> the existing request routes.

A voice clip is transcribed on this computer (ffmpeg + whisper.cpp); audio is
never sent to another service. Slack's own transcript is used when Slack
provides a complete one. Files are fetched only from files.slack.com with the
bot token, bounded in size and duration, and only for an authorized owner
message. Transcription runs in the background like image analysis, so the
Slack loop is never blocked, and its result is durable so a retry never
transcribes twice.
"""
import hashlib
import json
import os
import re
import shutil
from pathlib import Path
from urllib.request import Request, build_opener

from .conversation import ConversationRouter, _write

SIZE_LIMIT = 25_000_000
DURATION_LIMIT_MS = 10 * 60 * 1000


class AudioError(RuntimeError):
    pass


def settings(home, config):
    """Transcriber locations; defaults live in CAPO_HOME/tools, outside Git."""
    voice = config.get('voice', {})
    if not isinstance(voice, dict) or type(voice.get('enabled', True)) is not bool:
        raise ValueError('voice must be an object with an optional boolean enabled')
    tools = Path(home) / 'tools'
    return {'enabled': voice.get('enabled', True),
            'whisper_cli': str(voice.get('whisper_cli') or tools / 'bin' / 'whisper-cli'),
            'model': str(voice.get('model') or tools / 'models' / 'ggml-small.en.bin'),
            'ffmpeg': str(voice.get('ffmpeg') or shutil.which('ffmpeg') or '/opt/homebrew/bin/ffmpeg')}


def audio_files(event):
    return [f for f in event.get('files', []) if f.get('id') and
            (f.get('subtype') == 'slack_audio' or str(f.get('mimetype', '')).startswith('audio/'))]


def slack_transcript(info):
    """Slack's own transcript, only when complete and not truncated."""
    value = info.get('transcription') or {}
    preview = value.get('preview') or {}
    if value.get('status') == 'complete' and preview.get('content') and not preview.get('has_more'):
        return preview['content']
    return ''


def download(client, file_id, destination):
    from .slack_images import SlackRedirects, trusted_url
    if not re.fullmatch(r'F[A-Z0-9]+', file_id):
        raise AudioError('That voice message has an invalid Slack file ID.')
    try:
        info = client.files_info(file=file_id).get('file', {})
    except Exception:
        raise AudioError('I couldn’t open that voice message in Slack. Please send it again.') from None
    if info.get('size', SIZE_LIMIT + 1) > SIZE_LIMIT or info.get('duration_ms', 0) > DURATION_LIMIT_MS:
        raise AudioError('Please keep voice messages under 10 minutes.')
    text = slack_transcript(info)
    if text:
        return info, text
    url = trusted_url(info.get('url_private_download') or info.get('url_private', ''))
    token = os.environ.get('SLACK_BOT_TOKEN')
    if not token:
        raise AudioError('Slack file access is not configured on this computer.')
    with build_opener(SlackRedirects()).open(Request(url, headers={'Authorization': 'Bearer ' + token}), timeout=30) as response:
        raw = response.read(SIZE_LIMIT + 1)
    if len(raw) > SIZE_LIMIT:
        raise AudioError('Please keep voice messages under 10 minutes.')
    destination.write_bytes(raw)
    return info, ''


def transcribe(config, source, directory):
    """Convert to 16 kHz mono WAV and transcribe locally; returns plain text."""
    from .process import run_process, WorkerError
    for name in ('whisper_cli', 'model', 'ffmpeg'):
        if not Path(config[name]).exists():
            raise AudioError('Voice transcription isn’t set up on this computer yet.')
    wav = directory / 'audio.wav'
    try:
        run_process([config['ffmpeg'], '-nostdin', '-loglevel', 'error', '-y', '-i', str(source),
                     '-ar', '16000', '-ac', '1', '-c:a', 'pcm_s16le', str(wav)], directory, directory / 'convert-run', 120)
        run_process([config['whisper_cli'], '-m', config['model'], '-f', str(wav), '-nt', '-np',
                     '-otxt', '-of', str(directory / 'transcript')], directory, directory / 'transcribe-run', 600)
    except (WorkerError, FileNotFoundError, OSError):
        raise AudioError('I couldn’t transcribe that voice message. Please try again or type it.') from None
    finally:
        wav.unlink(missing_ok=True)
    text = ' '.join((directory / 'transcript.txt').read_text(errors='replace').split())
    if not text or re.fullmatch(r'\[[^\]]*\]', text):  # e.g. "[BLANK_AUDIO]"
        raise AudioError('I couldn’t hear any speech in that voice message. Please try again.')
    return text


class AudioConversation(ConversationRouter):
    def __init__(self, home, client, config):
        self.client, self.settings = client, settings(home, config)
        super().__init__(Path(home) / 'slack-audio')

    def _run(self, directory, context, schema, fd):
        try:
            parts = []
            for file_id in context['files']:
                source = directory / ('voice-' + file_id)
                info, text = download(self.client, file_id, source)
                try:
                    parts.append(text or transcribe(self.settings, source, directory))
                finally:
                    source.unlink(missing_ok=True)   # Keep the transcript, not the recording.
            transcript = ' '.join(parts)[:8000]
            _write(directory / 'transcript.json', {'text': transcript})
            reply = transcript
        except AudioError as exc:
            reply = str(exc)
        except Exception:
            reply = 'I couldn’t transcribe that voice message. Please try again or type it.'
        try:
            _write(directory / 'outcome.json', {'route': {'action': 'reply', 'repository': '', 'objective_id': '', 'reply': reply}})
        finally:
            os.close(fd)


def heard(home, event_id):
    """The saved transcript for an event, if it contained a voice message."""
    path = (Path(home) / 'slack-audio' / 'conversation' / hashlib.sha256(str(event_id).encode()).hexdigest()
            / 'transcript.json')
    try:
        return json.loads(path.read_text())['text']
    except (OSError, ValueError, KeyError):
        return ''


def context(service, event_id, body, text):
    """Called only after owner authorization; adds the spoken words to the request."""
    from .slack import authorized
    if not authorized(service.config, body, service.store):
        raise ValueError('Unauthorized voice request')
    files = [f['id'] for f in audio_files(body['event'])][:3]
    if not files:
        return text
    if not settings(service.store.home, service.config)['enabled']:
        raise AudioError('Voice messages are turned off. Please type your request.')
    if not hasattr(service, 'audio_conversation'):
        service.audio_conversation = AudioConversation(service.store.home, service.client, service.config)
    value = service.audio_conversation.poll(event_id, {'aliases': [], 'objectives': [], 'message': text, 'files': files})
    spoken = heard(service.store.home, event_id)
    if not spoken:
        raise AudioError(value['reply'])
    # The owner said these words; misrecognition is possible, so unclear consequential details need confirmation.
    return ((text + '\n\n') if text.strip() else '') + (
        'Owner voice message (automatic transcript; may contain recognition errors — confirm unclear names, '
        'amounts or dates before consequential actions):\n' + spoken)

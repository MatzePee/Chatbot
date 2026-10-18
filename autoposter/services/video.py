"""Bounded local MP4 import; no remote URLs/playlists, no shell, no video re-encoding."""
import json
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path


def prepare(filename, data):
    from . import media
    if not data or len(data)>50_000_000 or data[4:8]!=b'ftyp':
        raise ValueError('Video: nur lokale MP4-Dateien bis 50 MB.')
    if not shutil.which('ffprobe') or not shutil.which('ffmpeg'):
        raise ValueError('MP4-Import benötigt ffmpeg und ffprobe auf dem Server.')
    asset_id=uuid.uuid4(); directory=media.asset_dir(asset_id)
    try:
        with tempfile.TemporaryDirectory(prefix='autoposter-video-') as tmp:
            source=Path(tmp)/'input.mp4';source.write_bytes(data)
            probe=subprocess.run(['ffprobe','-v','error','-protocol_whitelist','file,pipe','-show_streams','-show_format','-of','json',str(source)],capture_output=True,timeout=20,check=True)
            info=json.loads(probe.stdout); streams=info['streams']; video=[s for s in streams if s['codec_type']=='video'];audio=[s for s in streams if s['codec_type']=='audio']
            if len(video)!=1 or video[0]['codec_name']!='h264' or len(audio)>1 or any(s['codec_name']!='aac' for s in audio):
                raise ValueError('MP4: genau ein H.264-Videostream, optional eine AAC-Audiospur unterstützt.')
            w,h=video[0]['width'],video[0]['height']
            if w<=0 or h<=0 or w*h>16_777_216:
                raise ValueError('MP4: ungültige/zu große Auflösung (max. 16 Megapixel).')
            directory.mkdir(parents=True,exist_ok=True);original=directory/'original.mp4'
            subprocess.run(['ffmpeg','-v','error','-nostdin','-protocol_whitelist','file,pipe','-i',str(source),'-map','0:v:0','-map','0:a:0?','-c','copy','-map_metadata','-1','-map_chapters','-1','-movflags','+faststart',str(original)],capture_output=True,timeout=30,check=True)
            thumb=Path(tmp)/'frame.jpg'
            subprocess.run(['ffmpeg','-v','error','-nostdin','-protocol_whitelist','file,pipe','-i',str(original),'-frames:v','1','-vf','scale=512:512:force_original_aspect_ratio=decrease',str(thumb)],capture_output=True,timeout=30,check=True)
            frame=thumb.read_bytes();normalized=original.read_bytes()
            if len(normalized)>50_000_000:raise ValueError('MP4: maximal 50 MB nach Aufbereitung.')
            media._write_thumbnails(directory,frame)
            return media.PreparedImage(asset_id,filename[:255],str(original.relative_to(media.media_root())),media.sha256_of(normalized),media._phash(frame),w,h,len(normalized),'video/mp4')
    except Exception as exc:
        shutil.rmtree(directory,ignore_errors=True)
        if isinstance(exc,ValueError):raise
        raise ValueError('MP4-Video konnte nicht geprüft werden; H.264/AAC und gültigen ersten Frame sicherstellen.') from None

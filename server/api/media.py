"""Source-audio discovery/audition and disposable cache management."""
from pathlib import Path
from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field
from server.core import media_cache, proc
from server.core.ffmpeg_tools import get_media_info

router = APIRouter(prefix='/media', tags=['media'])
class SourceRequest(BaseModel):
    path: str
class PreviewRequest(SourceRequest):
    track: int = Field(ge=1)
    start: float = Field(default=0, ge=0, allow_inf_nan=False)

def audio_tracks(path):
    streams=[s for s in get_media_info(path).get('streams',[]) if s.get('codec_type')=='audio']
    result=[]
    for n,stream in enumerate(streams,1):
        tags=stream.get('tags') or {}
        name=tags.get('title') or tags.get('handler_name') or 'Unnamed audio'
        result.append(dict(number=n,name=name,language=tags.get('language','und'),channels=stream.get('channels'),default=bool((stream.get('disposition') or {}).get('default'))))
    return result

@router.post('/audio-tracks')
def source_tracks(req:SourceRequest):
    try:return {'tracks':audio_tracks(req.path)}
    except Exception:raise HTTPException(400,'Could not inspect audio tracks. Check that the source file and FFmpeg are available.') from None

@router.post('/audio-preview')
def preview(req:PreviewRequest):
    try:
        tracks=audio_tracks(req.path)
        if req.track>len(tracks):raise ValueError()
        key=media_cache.fingerprint(req.path,['preview-v1',req.track,req.start])
        with media_cache.LOCK:
            media_cache.ROOT.mkdir(parents=True,exist_ok=True)
            path=media_cache.ROOT/(key+'.wav')
            if not path.exists():
                partial=media_cache.ROOT/(key+'.part.wav')
                try:
                    result=proc.run(['ffmpeg','-v','error','-y','-ss',str(req.start),'-i',req.path,'-t','12','-map',f'0:a:{req.track-1}','-vn','-ac','1','-ar','16000','-c:a','pcm_s16le',str(partial)],capture_output=True,text=True,timeout=30)
                    if result.returncode or partial.stat().st_size<=44:raise ValueError()
                    partial.replace(path)
                finally:partial.unlink(missing_ok=True)
            return Response(content=path.read_bytes(),media_type='audio/wav')
    except Exception:raise HTTPException(400,'Could not preview this track/time. Choose a point inside the video and check FFmpeg.') from None

@router.get('/cache')
def cache_status():return media_cache.status()

@router.post('/cache/clear')
def clear_cache():
    try:return media_cache.clear()
    except RuntimeError as exc:raise HTTPException(409,str(exc)) from None

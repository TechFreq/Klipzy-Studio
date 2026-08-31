"""
Core data models for the AI Video Clipper.
Pydantic models shared across the server API and processing pipeline.
"""

from typing import List, Optional
from pydantic import BaseModel, Field


class WordTimestamp(BaseModel):
    word: str
    start: float
    end: float
    probability: float = 1.0


class TranscriptSegment(BaseModel):
    id: int
    start: float
    end: float
    text: str
    words: List[WordTimestamp] = Field(default_factory=list)


class ViralityBreakdown(BaseModel):
    hook_score: float = 8.5
    flow_score: float = 8.0
    engagement_score: float = 9.0
    trend_potential: str = "High"
    hook_keywords: List[str] = Field(default_factory=list)


class ClipCandidate(BaseModel):
    id: str
    title: str
    start_time: float
    end_time: float
    duration: float
    score: float
    hook_text: str
    full_text: str
    reason: str
    virality: Optional[ViralityBreakdown] = None
    words: List[WordTimestamp] = Field(default_factory=list)


class ClipResult(BaseModel):
    clip_id: str
    title: str
    score: float
    start_time: float
    end_time: float
    duration: float
    hook_text: str
    output_file: str
    virality: Optional[ViralityBreakdown] = None
    words: List[WordTimestamp] = Field(default_factory=list)
    thumbnail_path: Optional[str] = None
    srt_path: Optional[str] = None
    vtt_path: Optional[str] = None
    ass_path: Optional[str] = None
    # Layout / camera / crop params used for potential re-render
    layout: str = ""
    cam_video: Optional[str] = None
    cam_scale: float = 0.3
    cam_position: str = "bottom-right"
    crop_x_offset: Optional[float] = None


class ProcessRequest(BaseModel):
    video_path: str
    vertical_crop: bool = True
    aspect_ratio: Optional[str] = "9:16"  # "9:16" | "1:1" | "4:5" | "16:9" | "full"
    max_clips: int = 5
    min_duration: float = 20.0
    max_duration: float = 60.0
    whisper_model: str = "base"
    language: Optional[str] = None
    use_audio_energy: bool = True
    use_llm: bool = False
    burn_captions: bool = True
    caption_style: Optional[str] = "viral_yellow"
    # Explicit override for the ASS caption font size used during initial clip generation.
    font_size: Optional[int] = None
    remove_silence: bool = False
    bleep_profanity: bool = False
    mute_profanity: bool = False
# ---- CapCut-style caption fine-tuning (optional; falls back to the preset's values) ----
    font_name: Optional[str] = None
    primary_color: Optional[str] = None
    highlight_color: Optional[str] = None
    outline_color: Optional[str] = None
    outline_width: Optional[int] = None
    chunk_size: Optional[int] = None
    uppercase: Optional[bool] = None
    bold: Optional[bool] = None
    italic: Optional[bool] = None
    position: Optional[int] = None  # ASS alignment 1-9 (2 = bottom-center, 8 = top-center...)
    intro_caption: Optional[str] = None
    intro_caption_duration: float = 3.0


class ProcessResponse(BaseModel):
    job_id: str
    status: str = "queued"
    clips: List[ClipResult] = Field(default_factory=list)
    error: Optional[str] = None


class ExportProjectRequest(BaseModel):
    video_path: str
    clips: List[dict] = Field(default_factory=list)
    format: str = "fcpxml"  # "fcpxml" | "edl" | "capcut"
    fps: float = 30.0


class ExportProjectResponse(BaseModel):
    export_path: str
    format: str
    message: str
    re_rendered: bool = False


class SubtitleRegenRequest(BaseModel):
    output_path: str
    words: List[dict] = Field(default_factory=list)
    style_preset: str = "viral_yellow"
    font_size: Optional[int] = None
    # ---- CapCut-style caption fine-tuning ---- caps
    font_name: Optional[str] = None
    primary_color: Optional[str] = None
    highlight_color: Optional[str] = None
    outline_color: Optional[str] = None
    outline_width: Optional[int] = None
    chunk_size: Optional[int] = None
    uppercase: Optional[bool] = None
    bold: Optional[bool] = None
    italic: Optional[bool] = None
    position: Optional[int] = None  # ASS alignment 1-9
    intro_caption: Optional[str] = None
    intro_caption_duration: float = 3.0
    source_video: Optional[str] = None
    clip_output_file: Optional[str] = None
    start_seconds: Optional[float] = None
    end_seconds: Optional[float] = None
    aspect_ratio: Optional[str] = "9:16"
    layout: str = ""  # "vertical" | "full" | "game_reaction"
    cam_video: Optional[str] = None
    cam_scale: float = 0.3
    cam_position: str = "bottom-right"
    crop_x_offset: Optional[float] = None
    re_render: bool = False


class ChatRequest(BaseModel):
    message: str
    conversation_history: List[dict] = Field(default_factory=list)
    clip_context: Optional[dict] = None


class ChatResponse(BaseModel):
    reply: str
    source: str = "ollama"  # 'ollama' | 'fallback'
class SocialMetadataRequest(BaseModel):
    title: str = ""
    hook_text: str = ""
    full_text: str = ""
    duration: float = 0.0
    platform: Optional[str] = "all"  # 'youtube', 'tiktok', 'instagram', 'linkedin', 'all'


class SocialMetadataResponse(BaseModel):
    title: str
    description: str
    hashtags: List[str]
    formatted_post: str
    disclaimer: str = "AI-generated metadata — accuracy and tone may vary. Review before publishing."


class TrimRequest(BaseModel):
    """Manual clip trim geometry: a single in/out selection from the source video."""
    video_path: str
    start_seconds: float
    end_seconds: float
    title: str = ""
    burn_captions: bool = False
    subtitle_path: Optional[str] = None
    caption_style: Optional[str] = None
    # ---- Caption fine-tuning (used when burn_captions is True) ---- captions
    font_name: Optional[str] = None
    primary_color: Optional[str] = None
    highlight_color: Optional[str] = None
    outline_color: Optional[str] = None
    outline_width: Optional[int] = None
    chunk_size: Optional[int] = None
    uppercase: Optional[bool] = None
    bold: Optional[bool] = None
    italic: Optional[bool] = None
    position: Optional[int] = None  # ASS alignment 1-9
    font_size: Optional[int] = None


class TrimResponse(BaseModel):
    clip_path: str
    title: str
    start_seconds: float
    end_seconds: float
    duration: float
    words: List[WordTimestamp] = Field(default_factory=list)


class CustomRenderRequest(BaseModel):
    """Custom re-render of a saved selection with a chosen output aspect layout."""
    video_path: str
    start_seconds: float
    end_seconds: float
    output_path: Optional[str] = None
    layout: str = "vertical"       # "vertical" | "full" | "game_reaction"
    aspect_ratio: Optional[str] = "9:16"
    crop_x_offset: Optional[float] = None
    burn_captions: bool = False
    subtitle_path: Optional[str] = None
    caption_style: Optional[str] = None
    font_name: Optional[str] = None
    primary_color: Optional[str] = None
    highlight_color: Optional[str] = None
    outline_color: Optional[str] = None
    outline_width: Optional[int] = None
    chunk_size: Optional[int] = None
    uppercase: Optional[bool] = None
    bold: Optional[bool] = None
    italic: Optional[bool] = None
    position: Optional[int] = None  # ASS alignment 1-9
    font_size: Optional[int] = None
    cam_video: Optional[str] = None
    cam_scale: float = 0.3
    cam_position: str = "bottom-right"


class DeleteProjectRequest(BaseModel):
    """Paths and job folders belonging to a saved project that may be safely removed.

    Only paths inside the engine's output directory are honored by the server;
    the source video is never deleted.
    """
    paths: List[str] = Field(default_factory=list)


class ExportMediaRequest(BaseModel):
    """Export a single existing rendered clip to a chosen container/codec format."""
    video_path: str
    format: str = "mp4"   # "mp4" | "mov" | "mkv" | "webm" | "av1" | "gif"
    output_path: Optional[str] = None


class ExportMediaResponse(BaseModel):
    export_path: str
    format: str
    duration: float
    message: str


class ExportCompileRequest(BaseModel):
    """Concatenate existing rendered clips into one highlights-reel media file."""
    clip_paths: List[str] = Field(default_factory=list)
    format: str = "mp4"   # "mp4" | "mov" | "mkv" | "webm" | "av1" | "gif"
    output_path: Optional[str] = None
    title: str = "highlights_reel"


class ExportCompileResponse(BaseModel):
    export_path: str
    format: str
    clip_count: int
    duration: float
    message: str


class ExportStandaloneRequest(BaseModel):
    """Export standalone assets: separate audio track (mp3/wav/flac) or standalone transcript/subtitles (txt/srt/vtt/json)."""
    video_path: Optional[str] = None
    clip_index: Optional[int] = None
    asset_type: str = "audio_mp3" # "audio_mp3", "audio_wav", "audio_flac", "audio_aac", "audio_m4a", "sub_srt", "sub_vtt", "transcript_txt", "transcript_json"
    output_path: Optional[str] = None


class ExportStandaloneResponse(BaseModel):
    export_path: str
    asset_type: str
    message: str


class ClipBundleRequest(BaseModel):
    """Export one rendered clip and its matching audio/subtitle assets together."""
    video_path: str
    output_dir: str
    title: str = "clip"
    format: str = "mp4"
    srt_path: Optional[str] = None
    ass_path: Optional[str] = None


class ThumbnailRequest(BaseModel):
    """Generate or extract a thumbnail poster from a video clip."""
    video_path: str
    timestamp: float = 0.5
    output_path: Optional[str] = None


class ThumbnailResponse(BaseModel):
    thumbnail_path: str
    message: str


class ClipBundleResponse(BaseModel):
    export_dir: str
    video_path: str
    audio_path: Optional[str] = None
    srt_path: Optional[str] = None
    ass_path: Optional[str] = None
    message: str


class DetectSilenceRequest(BaseModel):
    media_path: str
    noise_threshold_db: float = -30.0
    min_silence_duration: float = 0.6


class DetectSilenceResponse(BaseModel):
    intervals: List[dict] = Field(default_factory=list)
    total_silence: float
    silence_count: int


class RemoveSilenceRequest(BaseModel):
    video_path: str
    output_path: Optional[str] = None
    noise_threshold_db: float = -30.0
    min_silence_duration: float = 0.6
    pad_seconds: float = 0.08


class RemoveSilenceResponse(BaseModel):
    output_path: str
    original_duration: float
    cut_duration: float
    time_saved: float
    silence_intervals: List[dict] = Field(default_factory=list)
    message: str


class BleepMuteRequest(BaseModel):
    video_path: str
    output_path: Optional[str] = None
    mode: str = "bleep"  # "bleep" | "mute"
    timestamps: Optional[List[dict]] = None
    custom_words: Optional[List[str]] = None
    beep_freq: int = 1000


class BleepMuteResponse(BaseModel):
    output_path: str
    censored_count: int
    mode: str
    message: str


class CaptionPresetInfo(BaseModel):
    id: str
    name: str
    description: str
    font_name: str
    font_size: int
    primary_color: str
    highlight_color: str


class MultiAspectExportRequest(BaseModel):
    clip_path: str
    aspect_ratios: List[str] = Field(default_factory=lambda: ["9:16", "1:1", "4:5", "16:9"])
    output_dir: Optional[str] = None
    title: Optional[str] = None
    source_video: Optional[str] = None
    start_seconds: Optional[float] = None
    end_seconds: Optional[float] = None
    burn_captions: bool = True
    subtitle_path: Optional[str] = None


class MultiAspectExportResponse(BaseModel):
    exports: dict = Field(default_factory=dict)
    message: str


class OverlayRequest(BaseModel):
    video_path: str
    broll_path: str
    output_path: Optional[str] = None
    start_time: float = 0.0
    duration: float = 3.0
    scale: float = 0.85
    position: str = "center"  # "center" | "top-right" | "top-left" | "bottom-right" | "bottom-left"
    opacity: float = 1.0
    subtitle_path: Optional[str] = None


class OverlayResponse(BaseModel):
    output_path: str
    message: str


class EmojiSuggestRequest(BaseModel):
    segments: List[TranscriptSegment] = Field(default_factory=list)


class EmojiSuggestResponse(BaseModel):
    suggestions: List[dict] = Field(default_factory=list)


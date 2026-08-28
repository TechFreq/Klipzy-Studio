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


class ClipResult(BaseModel):
    clip_id: str
    title: str
    score: float
    start_time: float
    end_time: float
    duration: float
    hook_text: str
    output_file: str


class ProcessRequest(BaseModel):
    video_path: str
    vertical_crop: bool = True
    max_clips: int = 5
    min_duration: float = 20.0
    max_duration: float = 60.0
    whisper_model: str = "base"
    language: Optional[str] = None
    use_audio_energy: bool = True
    use_llm: bool = False


class ProcessResponse(BaseModel):
    job_id: str
    status: str = "queued"
    clips: List[ClipResult] = Field(default_factory=list)
    error: Optional[str] = None


class ChatRequest(BaseModel):
    message: str
    conversation_history: List[dict] = Field(default_factory=list)
    clip_context: Optional[dict] = None


class ChatResponse(BaseModel):
    reply: str
    source: str = "ollama"  # 'ollama' | 'fallback'
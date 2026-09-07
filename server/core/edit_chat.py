"""
AI Edit Chat - lets users chat with an AI assistant about their edits.
Uses Ollama (Gemma) locally with a smart fallback for when Ollama isn't installed.
"""

from typing import List, Optional, Dict

from server.models import ChatResponse


class EditChat:
    def __init__(self, model: str = "gemma2:2b"):
        self.model = model

    def chat(
        self,
        message: str,
        conversation_history: Optional[List[dict]] = None,
        clip_context: Optional[dict] = None,
    ) -> ChatResponse:
        history = conversation_history or []
        context = clip_context or {}

        system_prompt = """You are an AI video editing assistant for a local-first Long Form to Shorts studio.
You help creators choose clips, write hooks, captions, and titles for TikTok/Shorts/Reels.
Be concise, practical, and creative. You only know about the current clip context provided.
"""

        user_content = f"Current clip context: {context}\n\nUser message: {message}"

        try:
            from server.core import llm_client

            messages = [{"role": "system", "content": system_prompt}]
            messages.extend(history)
            messages.append({"role": "user", "content": user_content})

            reply = llm_client.chat(messages, model=self.model)
            if not reply:
                raise RuntimeError("empty reply")
            # Report which backend answered so the UI can be honest about it.
            return ChatResponse(reply=reply, source=llm_client.load_config()["backend"])
        except Exception:
            # Smart fallback - rule-based editing advice
            return ChatResponse(reply=self._fallback_reply(message, context), source="fallback")

    def _fallback_reply(self, message: str, context: dict) -> str:
        msg_lower = message.lower()

        if any(k in msg_lower for k in ["hook", "opening", "first line", "title"]):
            return (
                "Here are 3 hook ideas for this clip:\n"
                "1. Start with the most shocking statement: \"Nobody talks about this...\"\n"
                "2. Open with a question: \"Ever wondered why...?\"\n"
                "3. Tease the payoff: \"Wait until you hear what happens next...\"\n\n"
                "💡 Tip: The first 2 seconds decide whether viewers keep watching!"
            )
        if any(k in msg_lower for k in ["caption", "description", "text", "subtitle"]):
            return (
                "Caption suggestions:\n"
                "• Keep it under 150 characters\n"
                "• Ask a question to drive comments\n"
                "• Use 3-5 relevant hashtags\n"
                "• Example: \"The secret nobody tells you about [topic] 🤯 #shorts\""
            )
        if any(k in msg_lower for k in ["clip", "cut", "shorter", "longer", "edit"]):
            return (
                "Editing tips:\n"
                "• Shorts perform best at 15-30 seconds\n"
                "• Cut right after the punchline\n"
                "• Keep a strong hook in the first 2 seconds\n"
                "• Add captions for silent viewing (80%+ watch muted)"
            )
        if any(k in msg_lower for k in ["hashtag", "tag", "trend"]):
            return (
                "Hashtag strategy:\n"
                "• 3-5 niche hashtags (not #fyp spam)\n"
                "• Mix broad + specific: #podcast #entrepreneurship #shorts\n"
                "• Check trending audio in your niche"
            )
        return (
            "I'm your AI editing assistant! I can help with:\n"
            "• Hook & title ideas\n"
            "• Caption & hashtag suggestions\n"
            "• Clip timing & pacing advice\n\n"
            "💡 Ollama is Klipzy's local AI engine. Enable it in Shorts Clipper or install/start Ollama from Setup if it is not available yet."
        )
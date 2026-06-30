import streamlit as st
import base64
import asyncio
import sys
import os
import json
import time
import io
import requests
from datetime import datetime
from pathlib import Path
from google import genai
from google.genai import types
from pydantic import BaseModel, Field
from typing import List, Optional
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

# ─── Windows Asyncio Fix ───
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

# ─── Page Config ───
st.set_page_config(
    page_title="The Mahabharata Mindset — Post Generator",
    page_icon="⚔️",
    layout="wide",
    initial_sidebar_state="expanded"
)

# ═══════════════════════════════════════════════════════════════
# SECTION 1: PYDANTIC SCHEMAS FOR THE 3-AGENT PIPELINE
# ═══════════════════════════════════════════════════════════════

# ── Agent 1: Brand Strategist ──
class StrategistBrief(BaseModel):
    epic_character: str = Field(description="The Mahabharata figure to anchor the post. Examples: Karna, Abhimanyu, Ghatotkach, Draupadi, Bhishma, Vidura, Krishna, Arjuna, Eklavya, Yudhishthira, Duryodhana, Shakuni")
    story_moment: str = Field(description="The specific story beat from the epic. Be precise — name the scene, the characters present, the stakes. Example: 'Karna gives away his kavach-kundal to Indra in disguise, knowing it will cost him his life in battle'")
    modern_parallel: str = Field(description="The modern struggle this maps to. Be visceral and specific, not abstract. Example: 'Giving everything to a job that will never promote you'")
    emotional_core: str = Field(description="One word — the dominant feeling the post must evoke. Examples: betrayal, defiance, sacrifice, isolation, duty, rage, stillness")
    recommended_template: str = Field(description="Must be exactly one of: 'Quote Card', 'Carousel Slide', 'Character Spotlight', 'Reflection Post', 'List / Tips', 'Series Cover'")
    recommended_format: str = Field(description="Must be exactly one of: '1:1 Feed Post', '9:16 Reel'")
    strategic_rationale: str = Field(description="2-3 sentences explaining why this character, this moment, and this template are the highest-leverage combination for the user's input.")

# ── Agent 2: Dharmic Copywriter (per-template outputs) ──
class CarouselSlideItem(BaseModel):
    slide_num: int = Field(description="Slide number (1-based)")
    title: str = Field(description="Slide title. Fragments over sentences. Em-dashes over commas. Period-separated staccato. Must feel like a war drum.")
    body: str = Field(description="Body text. Maximum 2 sentences. Prefer 1. For slide 1 (cover), this should be empty string ''.")

class CopywriterOutput(BaseModel):
    template_type: str = Field(description="The template used. Must match the strategist's recommendation.")

    # Quote Card
    quote_text: Optional[str] = Field(None, description="The quote. Use linebreaks for rhythm.")
    quote_source: Optional[str] = Field(None, description="Scripture source with chapter.verse format")
    quote_sanskrit: Optional[str] = Field(None, description="Sanskrit transliteration with proper diacritical marks (ā, ī, ū, ṛ, ṣ, ṭ, ṇ, ñ)")

    # Carousel
    carousel_tag: Optional[str] = Field(None, description="Series tag in caps, e.g. LESSONS FROM KARNA")
    carousel_total: Optional[int] = Field(None, description="Total slides (usually 5-6)")
    carousel_slides: Optional[List[CarouselSlideItem]] = Field(None, description="Complete list of slides")

    # Character Spotlight
    char_name: Optional[str] = Field(None, description="Character name")
    char_title: Optional[str] = Field(None, description="Epithet. Must be cinematic. 'The Boy Who Walked Into The Impossible' not 'A Brave Warrior'")
    char_traits: Optional[List[str]] = Field(None, description="Exactly 4 single-word traits")
    char_quote: Optional[str] = Field(None, description="A cinematic scene-description, NOT a motivational platitude. Must set the scene and punch.")

    # Reflection Post
    reflection_tag: Optional[str] = Field(None, description="Tag line")
    reflection_title: Optional[str] = Field(None, description="Title. Fragment stacking. Em-dashes. Use linebreaks.")
    reflection_body: Optional[str] = Field(None, description="1 sentence maximum. Gut-punch, not explanation.")

    # List / Tips
    list_tag: Optional[str] = Field(None, description="Tag line")
    list_title: Optional[str] = Field(None, description="Title")
    list_items: Optional[List[str]] = Field(None, description="3-5 items. Each under 12 words.")

    # Series Cover
    cover_label: Optional[str] = Field(None, description="Series label")
    cover_number: Optional[int] = Field(None, description="Series number")
    cover_title: Optional[str] = Field(None, description="Big title. Use linebreaks.")
    cover_subtitle: Optional[str] = Field(None, description="Subtitle hook")

# ── Agent 3: Adversarial Editor ──
class EditorOutput(BaseModel):
    approved: bool = Field(description="True if the copy passed all filters without changes. False if rewrites were needed.")
    audit_log: str = Field(description="Detailed log of what was tested and what was changed. Include which filters triggered.")
    # Same fields as CopywriterOutput — the editor returns the final version
    template_type: str
    quote_text: Optional[str] = None
    quote_source: Optional[str] = None
    quote_sanskrit: Optional[str] = None
    carousel_tag: Optional[str] = None
    carousel_total: Optional[int] = None
    carousel_slides: Optional[List[CarouselSlideItem]] = None
    char_name: Optional[str] = None
    char_title: Optional[str] = None
    char_traits: Optional[List[str]] = None
    char_quote: Optional[str] = None
    reflection_tag: Optional[str] = None
    reflection_title: Optional[str] = None
    reflection_body: Optional[str] = None
    list_tag: Optional[str] = None
    list_title: Optional[str] = None
    list_items: Optional[List[str]] = None
    cover_label: Optional[str] = None
    cover_number: Optional[int] = None
    cover_title: Optional[str] = None
    cover_subtitle: Optional[str] = None

# ── Agent 4: Caption & Hashtag Writer ──
class CaptionOutput(BaseModel):
    caption: str = Field(description="The full Instagram caption (multi-line). Includes hook, story, and CTA.")
    hashtags: List[str] = Field(description="15-25 hashtags relevant to the post and brand.")
    alt_text: str = Field(description="Accessibility alt text describing the visual post.")

class VoiceDirectionOutput(BaseModel):
    tagged_script: str = Field(description="The exact approved words, unchanged, with v3 [tags] and [pause] inserted. Removing all [bracketed] tokens must reproduce the original words verbatim.")
    base_stability: float = Field(description="ElevenLabs stability 0.0-1.0 for the whole clip. Lower = more emotional (0.25-0.35 for rage/grief, 0.5-0.6 for stillness).")
    base_style: float = Field(description="ElevenLabs style exaggeration 0.0-1.0. Higher = more dramatic delivery.")
    direction_notes: str = Field(description="One sentence on the intended performance arc, for the log.")



# ═══════════════════════════════════════════════════════════════
# SECTION 2: AGENT SYSTEM PROMPTS & FUNCTIONS
# ═══════════════════════════════════════════════════════════════

STRATEGIST_SYSTEM_PROMPT = """You are the Brand Strategist for 'The Mahabharata Mindset' — an Instagram brand that translates ancient Indian epic wisdom into stoic, anti-victimhood content for modern professionals.

Your job: Analyze the user's raw input (a modern struggle, pain point, or topic) and select the single highest-leverage angle to build a post around.

RULES:
1. Always anchor in a SPECIFIC story moment from the Mahabharata. Not vague references — name the scene, the characters present, the emotional stakes.
2. The modern parallel must be visceral and specific, not abstract self-help language.
3. The emotional core must be ONE word that captures the dominant feeling.
4. Choose the template that best serves the story:
   - Quote Card: For direct Bhagavad Gita verses or epic dialogue
   - Carousel Slide: For multi-part story breakdowns or lesson sequences
   - Character Spotlight: For deep character studies with traits + cinematic quotes
   - Reflection Post: For single-image gut-punch posts with minimal text
   - List / Tips: For tactical, numbered breakdowns
   - Series Cover: For launching multi-part content series
5. Choose the format:
   - '1:1 Feed Post' for Quote Cards, Carousels, Lists
   - '9:16 Reel' for Character Spotlights and Reflection Posts that benefit from vertical drama

Think like a war council strategist. Every post is a weapon — choose the sharpest one."""


COPYWRITER_SYSTEM_PROMPT = """You are the Dharmic Copywriter for 'The Mahabharata Mindset'. You write Instagram post copy that hits like a war drum.

YOUR VOICE RULES — THESE ARE NON-NEGOTIABLE:

1. TITLES: Fragments over sentences. Em-dashes over commas. Period-separated staccato.
   ✅ "Everything he had. Everything he was. Still not enough."
   ✅ "Every institution failed him — his teachers, his family, his kingdom."
   ✅ "Arjuna walked away from a million soldiers. And won."
   ❌ "Karna teaches us valuable lessons about perseverance"
   ❌ "The importance of staying strong in difficult times"

2. BODY TEXT: Maximum 2 sentences. Prefer 1. If it can be said in 8 words, don't use 12.
   ✅ "It was never about what he gave."
   ✅ "Duryodhana took the army. You would have too."
   ✅ "What breaks most people is the accumulation — rejection layered on rejection. Karna carried all of it and never let it become his identity."
   ❌ Long explanatory paragraphs about life lessons

3. Never EXPLAIN the lesson — DELIVER it. The reader must feel punched, not lectured.

4. Second-person address ("You would have too") is a power move — use sparingly but deliberately.

5. Character quotes must be CINEMATIC SCENE-DESCRIPTIONS, not motivational platitudes.
   ✅ "Seven of the greatest warriors surrounded him. He was sixteen. He fought until there was nothing left to fight with."
   ✅ "Every warrior feared Karna's greatest weapon. Ghatotkacha walked straight into it — so nobody else would have to."
   ❌ "He showed great courage and never gave up"

6. Sanskrit transliterations MUST use proper diacritical marks: ā, ī, ū, ṛ, ṣ, ṭ, ṇ, ñ, ś

7. Carousel covers (slide 1) have a big title and EMPTY body text.

8. BANNED WORDS: delve, testament, tapestry, crucial, journey, landscape, navigate, unlock, empower, leverage, "In today's fast-paced world", "In the annals of", "stands as a"

HERE ARE REAL EXAMPLES FROM THE BRAND — MATCH THIS EXACT QUALITY:

=== CAROUSEL SLIDE (Cover) ===
Tag: "LESSONS FROM KARNA"
Title: "5 Things Karna Teaches About Rejection"
Body: ""

=== CAROUSEL SLIDE (Interior) ===
Tag: "LESSONS FROM KARNA"
Title: "He was denied entry — for his birth, not his skill."
Body: "The greatest archer of his generation was told the school wasn't for him. The reason had nothing to do with ability."

=== CAROUSEL SLIDE (Interior) ===
Tag: "LESSONS FROM KARNA"
Title: "Humiliated in open court. He stood still."
Body: "In front of kings, warriors and the woman he admired — they questioned his right to even be in the room. He didn't move."

=== CAROUSEL SLIDE (Interior) ===
Tag: "LESSONS FROM KARNA"
Title: "Birthright, alliance, recognition — all arrived after the point of no return."
Body: "By the time the world was ready to acknowledge him, he had already built himself without its approval."

=== CAROUSEL SLIDE (Interior) ===
Tag: "LESSONS FROM KARNA"
Title: "Every institution failed him — his teachers, his family, his kingdom."
Body: "What breaks most people is the accumulation — rejection layered on rejection. Karna carried all of it and never let it become his identity."

=== REFLECTION POST ===
Tag: "ANCIENT LESSON · MODERN LIFE"
Title: "KARNA\nEverything he had. Everything he was. Still not enough."
Body: "It was never about what he gave."

=== REFLECTION POST (Reel) ===
Tag: "ANCIENT LESSON · MODERN LIFE"
Title: "Arjuna walked away from a million soldiers. And won."
Body: "Duryodhana took the army. You would have too."

=== QUOTE CARD ===
Quote: "Whatever a great person does, others follow.\nWhatever standard they set, the world follows."
Source: "Bhagavad Gita 3.21"
Sanskrit: "Yad yad ācarati śreṣṭhas tat tad evetaro janaḥ"

=== CHARACTER SPOTLIGHT ===
Name: "Abhimanyu"
Title: "The Boy Who Walked Into The Impossible"
Traits: ["Fearlessness", "Loyalty", "Brilliance", "Resilience"]
Quote: "Seven of the greatest warriors surrounded him. He was sixteen. He fought until there was nothing left to fight with."

=== CHARACTER SPOTLIGHT ===
Name: "Ghatotkach"
Title: "The Sacrifice That Won The War"
Traits: ["Ferocity", "Love", "Honour", "Selflessness"]
Quote: "Every warrior feared Karna's greatest weapon. Ghatotkacha walked straight into it — so nobody else would have to."

NOW WRITE. Match this standard or stay silent."""


EDITOR_SYSTEM_PROMPT = """You are the Adversarial Editor for 'The Mahabharata Mindset'. Your job is to review draft copy and either approve it or rewrite it.

You run FIVE merciless filters:

1. THE ARJUNA TEST: If Lord Krishna said this to Arjuna on the battlefield of Kurukshetra, would Arjuna string his Gandiva and charge — or would he scroll past? If it sounds like a generic LinkedIn "hustle bro" post or a corporate motivational poster, REWRITE IT.

2. THE BUZZWORD PURGE: Hard-reject ANY occurrence of: "In today's fast-paced world", "delve", "testament", "tapestry", "crucial", "journey", "landscape", "navigate", "unlock", "empower", "leverage", "In the annals of", "stands as a", "resonates", "pivotal". If found, rewrite the offending text.

3. THE CADENCE CHECK: Read every title aloud. Does it land with the weight of a war drum? Or does it sound like a blog headline? Titles must use fragment stacking, em-dashes, and period-separated staccato. If a title is a complete grammatical sentence, it's probably wrong.

4. THE BREVITY BLADE: 
   - Body text: 2 sentences maximum. Prefer 1. If a sentence exceeds 20 words, split or kill it.
   - Character quotes: 3 sentences maximum. Must be cinematic scene-setting, not generic praise.
   - Carousel cover (slide 1) body MUST be empty string.

5. THE PROPER-NOUN GUARDIAN: Every character name MUST match the canonical Mahabharata roster exactly. The ONLY valid spellings are: Karna, Arjuna, Draupadi, Krishna, Bhishma, Duryodhana, Yudhishthira, Bhima, Nakula, Sahadeva, Abhimanyu, Ghatotkach, Drona, Vidura, Kunti, Gandhari, Dhritarashtra, Pandu, Shakuni, Eklavya, Parashurama, Ashwatthama, Shikhandi, Barbarik, Subhadra, Uttara, Virata, Satyaki, Kritavarma, Shalya, Jayadratha, Dushasana, Shakuntala, Amba, Hidimba. If ANY name is misspelled (e.g. "Drapaupad", "Karan", "Dropadi"), IMMEDIATELY correct it using the canonical spelling and set approved=false. This is NON-NEGOTIABLE — the voiceover engine will mispronounce garbled names.

COMPARISON BENCHMARK — the copy must feel like it belongs alongside these:
- "Everything he had. Everything he was. Still not enough."
- "Duryodhana took the army. You would have too."
- "Seven of the greatest warriors surrounded him. He was sixteen."
- "Every institution failed him — his teachers, his family, his kingdom."

If the draft passes all 5 filters, set approved=true and return it unchanged.
If any filter fails, set approved=false, rewrite the offending fields, and explain what you changed in audit_log."""

CAPTION_SYSTEM_PROMPT = """You are the Caption & Hashtag Writer for 'The Mahabharata Mindset'.
Your job is to write the Instagram caption for the post that the Editor just approved.

RULES FOR THE CAPTION:
1. HOOK: The first line must grab attention and mirror the post's title energy.
2. STORY: 2-4 short paragraphs maximum. Explain the modern parallel and the ancient lesson.
3. TONE: Solemn, resolute, stoic. No emojis except maybe one at the very end (e.g. ⚔️, 🏹).
4. CTA: End with a call to action (e.g., "Follow @TheMahabharataMindset for daily epic wisdom." or "Save this for when you need to remember.")
5. HASHTAGS: Provide 15-25 hashtags. 
   - MUST INCLUDE: #TheMahabharataMindset #Mahabharata #Dharma #AncientWisdom #Stoicism
   - BANNED HASHTAGS: #GrindDontStop, #BossBabe, #HustleHarder, #MotivationDaily
   - Mix in character-specific tags (e.g., #Karna, #BhagavadGita) and broad mindset tags.

Return the caption (formatted with line breaks), the list of hashtags, and a simple alt_text description of the post's visual content."""


# ═══════════════════════════════════════════════════════════════
# SECTION 2B: ARCHIVE PERSISTENCE
# ═══════════════════════════════════════════════════════════════

ARCHIVE_PATH = Path(__file__).parent / "tmm_archive.json"

def load_archive() -> list:
    """Load the archive from disk."""
    if ARCHIVE_PATH.exists():
        try:
            with open(ARCHIVE_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return []
    return []

def save_to_archive(user_input: str, brief: dict, final: dict, caption_data: dict = None):
    """Append a generation to the archive."""
    archive = load_archive()
    entry = {
        "id": len(archive) + 1,
        "timestamp": datetime.now().isoformat(),
        "user_input": user_input,
        "brief": brief,
        "final": final,
        "caption": caption_data
    }
    archive.append(entry)
    with open(ARCHIVE_PATH, "w", encoding="utf-8") as f:
        json.dump(archive, f, indent=2, ensure_ascii=False)
    return entry

def extract_title_from_final(final: dict) -> str:
    """Extract the primary title/text from a final output for display."""
    t = final.get("template_type", "")
    if t == "Quote Card":
        return (final.get("quote_text") or "")[:80]
    elif t == "Carousel Slide":
        slides = final.get("carousel_slides") or []
        if slides:
            s = slides[0] if isinstance(slides[0], dict) else slides[0].__dict__
            return s.get("title", "")[:80]
        return final.get("carousel_tag", "")[:80]
    elif t == "Character Spotlight":
        return f"{final.get('char_name', '')} — {final.get('char_title', '')}"
    elif t == "Reflection Post":
        return (final.get("reflection_title") or "").replace("\n", " ")[:80]
    elif t == "List / Tips":
        return (final.get("list_title") or "")[:80]
    elif t == "Series Cover":
        return (final.get("cover_title") or "").replace("\n", " ")[:80]
    return "Untitled"

def get_memory_summary(max_entries: int = 30) -> str:
    """Build a summary of past generations for the Strategist to avoid repeats."""
    archive = load_archive()
    if not archive:
        return ""
    recent = archive[-max_entries:]
    lines = []
    for entry in recent:
        b = entry.get("brief", {})
        f = entry.get("final", {})
        title = extract_title_from_final(f)
        lines.append(f"- Character: {b.get('epic_character', '?')} | Emotional Core: {b.get('emotional_core', '?')} | Template: {f.get('template_type', '?')} | Title: {title}")
    return "\n".join(lines)


def get_gemini_client(api_key: str):
    return genai.Client(api_key=api_key)


def run_strategist(client, user_input: str) -> dict:
    """Agent 1: Analyze input and produce a strategic brief."""
    # Inject memory of past generations to avoid repeats
    memory = get_memory_summary()
    memory_block = ""
    if memory:
        memory_block = (
            "\n\nIMPORTANT — CONTENT ALREADY PUBLISHED (DO NOT REPEAT THESE ANGLES):\n"
            f"{memory}\n\n"
            "You MUST choose a DIFFERENT character + story moment + emotional core combination. "
            "If the same character is reused, the story moment and emotional angle must be completely different.\n"
        )
    prompt = f"Analyze this input and produce a strategic brief for an Instagram post:\n\n{user_input}{memory_block}"
    response = client.models.generate_content(
        model='gemini-2.5-flash',
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=StrategistBrief,
            system_instruction=STRATEGIST_SYSTEM_PROMPT,
            temperature=0.8,
        )
    )
    return json.loads(response.text)


def run_copywriter(client, brief: dict, user_input: str) -> dict:
    """Agent 2: Write the actual copy based on the strategist's brief."""
    prompt = (
        f"STRATEGIST BRIEF:\n"
        f"- Epic Character: {brief['epic_character']}\n"
        f"- Story Moment: {brief['story_moment']}\n"
        f"- Modern Parallel: {brief['modern_parallel']}\n"
        f"- Emotional Core: {brief['emotional_core']}\n"
        f"- Template: {brief['recommended_template']}\n"
        f"- Format: {brief['recommended_format']}\n"
        f"- Rationale: {brief['strategic_rationale']}\n\n"
        f"ORIGINAL USER INPUT: {user_input}\n\n"
        f"Write the post copy for the '{brief['recommended_template']}' template. "
        f"Fill ONLY the fields relevant to this template. Leave all other fields as null."
    )
    response = client.models.generate_content(
        model='gemini-2.5-flash',
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=CopywriterOutput,
            system_instruction=COPYWRITER_SYSTEM_PROMPT,
            temperature=0.9,
        )
    )
    return json.loads(response.text)


def run_editor(client, draft: dict, brief: dict) -> dict:
    """Agent 3: Review and potentially rewrite the copy."""
    prompt = (
        f"STRATEGIST BRIEF:\n"
        f"- Epic Character: {brief['epic_character']}\n"
        f"- Story Moment: {brief['story_moment']}\n"
        f"- Emotional Core: {brief['emotional_core']}\n\n"
        f"COPYWRITER DRAFT:\n{json.dumps(draft, indent=2)}\n\n"
        f"Review this draft against all 4 filters. Return the final publication-ready version."
    )
    response = client.models.generate_content(
        model='gemini-2.5-flash',
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=EditorOutput,
            system_instruction=EDITOR_SYSTEM_PROMPT,
            temperature=0.4,
        )
    )
    return json.loads(response.text)


def run_caption_writer(client, final_post: dict, brief: dict) -> dict:
    """Agent 4: Write the Instagram caption and hashtags."""
    prompt = (
        f"STRATEGIST BRIEF:\n"
        f"- Epic Character: {brief.get('epic_character', 'N/A')}\n"
        f"- Story Moment: {brief.get('story_moment', 'N/A')}\n"
        f"- Emotional Core: {brief.get('emotional_core', 'N/A')}\n\n"
        f"FINAL APPROVED POST CONTENT:\n{json.dumps(final_post, indent=2)}\n\n"
        f"Write the Instagram caption, provide 15-25 hashtags, and a simple alt_text description of the post."
    )
    response = client.models.generate_content(
        model='gemini-2.5-flash',
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=CaptionOutput,
            system_instruction=CAPTION_SYSTEM_PROMPT,
            temperature=0.7,
        )
    )
    return json.loads(response.text)


def run_full_pipeline(api_key: str, user_input: str, progress_callback=None):
    """Run all 4 agents in sequence."""
    client = get_gemini_client(api_key)
    
    if progress_callback:
        progress_callback("strategist", "running")
    brief = run_strategist(client, user_input)
    if progress_callback:
        progress_callback("strategist", "done", brief)
    
    if progress_callback:
        progress_callback("copywriter", "running")
    draft = run_copywriter(client, brief, user_input)
    if progress_callback:
        progress_callback("copywriter", "done", draft)
    
    if progress_callback:
        progress_callback("editor", "running")
    final = run_editor(client, draft, brief)
    if progress_callback:
        progress_callback("editor", "done", final)
        
    if progress_callback:
        progress_callback("caption", "running")
    caption_data = run_caption_writer(client, final, brief)
    if progress_callback:
        progress_callback("caption", "done", caption_data)
    
    return brief, draft, final, caption_data


def run_copy_only(api_key: str, brief: dict, user_input: str):
    """Re-run only agents 2+3+4 with the same strategist brief."""
    client = get_gemini_client(api_key)
    draft = run_copywriter(client, brief, user_input)
    final = run_editor(client, draft, brief)
    caption_data = run_caption_writer(client, final, brief)
    return draft, final, caption_data


# ═══════════════════════════════════════════════════════════════
# SECTION 2C: GOOGLE DRIVE & INSTAGRAM API INTEGRATION
# ═══════════════════════════════════════════════════════════════

import google.auth._helpers
import email.utils

def upload_to_gdrive(image_bytes: bytes, filename: str, service_account_path: str) -> str:
    """Uploads an image to Google Drive and makes it public. Returns the direct URL."""
    
    original_utcnow = None
    try:
        # 1. Dynamically fix local clock skew
        r = requests.get('https://www.googleapis.com/')
        if 'Date' in r.headers:
            google_time = email.utils.parsedate_to_datetime(r.headers['Date']).timestamp()
            offset = google_time - time.time()
            original_utcnow = google.auth._helpers.utcnow
            
            # Monkeypatch utcnow just for the auth generation
            google.auth._helpers.utcnow = lambda: datetime.utcfromtimestamp(time.time() + offset)
            
        # 2. Authenticate using fixed time
        creds = service_account.Credentials.from_service_account_file(
            service_account_path, 
            scopes=["https://www.googleapis.com/auth/drive"]
        )
        service = build('drive', 'v3', credentials=creds)
        
        # We need a folder ID or we just upload to root
        # If the user provided a folder ID in the sidebar, use it. Otherwise try to search by name.
        if "sidebar_gdrive_folder_id" in st.session_state and st.session_state["sidebar_gdrive_folder_id"]:
            folder_id = st.session_state["sidebar_gdrive_folder_id"]
        else:
            results = service.files().list(q="name='TMM Posts' and mimeType='application/vnd.google-apps.folder'", spaces='drive').execute()
            items = results.get('files', [])
            folder_id = items[0]['id'] if items else None
            
        if not folder_id:
            raise Exception("Could not find the 'TMM Posts' folder. Please ensure you shared it with the Service Account email, or paste the exact Folder ID in the sidebar.")
        
        file_metadata = {'name': filename, 'parents': [folder_id]}
            
        media = MediaIoBaseUpload(io.BytesIO(image_bytes), mimetype='image/png', resumable=True)
        file = service.files().create(body=file_metadata, media_body=media, fields='id, webContentLink', supportsAllDrives=True).execute()
        file_id = file.get('id')
        
        # Make public
        permission = {'type': 'anyone', 'role': 'reader'}
        service.permissions().create(fileId=file_id, body=permission).execute()
        
        # Construct the direct URL
        return f"https://lh3.googleusercontent.com/d/{file_id}"
    except Exception as e:
        return f"Error uploading to Drive: {str(e)}"
    finally:
        # Restore the original function to keep the environment clean
        if original_utcnow:
            google.auth._helpers.utcnow = original_utcnow

def upload_to_imgbb(image_bytes: bytes) -> str:
    """Uploads an image to FreeImageHost anonymously and returns the direct URL. No quota limits."""
    try:
        # This is a public API key for FreeImage.host (no account required)
        api_key = "6d207e02198a847aa98d0a2a901485a5"
        url = "https://freeimage.host/api/1/upload"
        payload = {
            "key": api_key,
            "action": "upload",
            "format": "json"
        }
        files = {
            "source": image_bytes
        }
        res = requests.post(url, data=payload, files=files)
        if res.status_code == 200:
            return res.json()["image"]["url"]
        else:
            return f"Error uploading to Image Host: {res.text}"
    except Exception as e:
        return f"Error uploading to Image Host: {str(e)}"

def upload_to_catbox(file_path: str) -> str:
    """Uploads an MP4 to litterbox.catbox.moe (1 hour expiry) for Instagram Reel publishing."""
    try:
        url = "https://litterbox.catbox.moe/api"
        data = {"reqtype": "fileupload", "time": "1h"}
        with open(file_path, "rb") as f:
            files = {"fileToUpload": f}
            res = requests.post(url, data=data, files=files)
        if res.status_code == 200:
            return res.text.strip()
        else:
            return f"Error uploading to Catbox: {res.text}"
    except Exception as e:
        return f"Error uploading to Catbox: {str(e)}"

def publish_to_instagram_single(image_url: str, caption_text: str, ig_user_id: str, access_token: str, media_type: str = "IMAGE") -> str:
    """Publish a single image to Instagram."""
    base_url = f"https://graph.facebook.com/v22.0/{ig_user_id}/media"
    
    # 1. Create Container
    payload = {
        "caption": caption_text,
        "access_token": access_token
    }
    
    if media_type == "REELS":
        payload["media_type"] = "REELS"
        payload["video_url"] = image_url
    else:
        payload["image_url"] = image_url
        
    r = requests.post(base_url, data=payload)
    if r.status_code != 200:
        return f"Container Error: {r.text}"
    
    creation_id = r.json().get("id")
    
    # 2. Wait for processing
    status_url = f"https://graph.facebook.com/v22.0/{creation_id}?fields=status_code&access_token={access_token}"
    max_wait = 30
    while max_wait > 0:
        s_req = requests.get(status_url)
        if s_req.status_code == 200:
            status = s_req.json().get("status_code")
            if status == "FINISHED":
                break
            elif status == "ERROR":
                return "Error processing image on Instagram servers."
        time.sleep(2)
        max_wait -= 2
        
    # 3. Publish
    publish_url = f"https://graph.facebook.com/v22.0/{ig_user_id}/media_publish"
    pub_payload = {
        "creation_id": creation_id,
        "access_token": access_token
    }
    p_req = requests.post(publish_url, data=pub_payload)
    if p_req.status_code == 200:
        return f"Success! Post ID: {p_req.json().get('id')}"
    else:
        return f"Publish Error: {p_req.text}"


# ── Agent 5: Voice Director ──
VOICE_DIRECTOR_SYSTEM_PROMPT = """You are the VOICE DIRECTOR for 'The Mahabharata Mindset'. You do not write copy. You direct PERFORMANCE.
You receive an already-approved narration script (a title + body, fixed and final) and the post's emotional_core. Your job: annotate that script with ElevenLabs v3 audio tags so the voiceover lands like a cinematic trailer and holds the viewer to the last word.

═══ THE IRON RULE — NON-NEGOTIABLE ═══
You must NOT change, add, remove, reorder, or respell a SINGLE spoken word. The words you return, with all [bracketed tags] deleted, must be byte-for-byte identical to the words you were given. The on-screen text is locked to these exact words — if you alter them, the video breaks. You may ONLY insert [tags] between words or at the start of a line. Pauses are created with the [pause] tag, NEVER by adding punctuation or ellipses.

═══ RETENTION MODEL — perform to this arc ═══
1. THE HOOK (first line): open with tension, not warmth. Tag for intensity, intrigue, or a hushed confession — something that makes a thumb stop. Never open flat.
2. THE ESCALATION (middle): vary the delivery line-to-line. A pattern that never changes is a pattern people scroll past. Alternate quiet and forceful. Place a [pause] right BEFORE the most important word so the viewer leans in.
3. THE PAYOFF (final line): slow down. Lower the energy or sharpen it to a point. The last line should feel like a verdict, delivered with weight. End on stillness, not a rush.

═══ V3 AUDIO TAG VOCABULARY (use only these) ═══
Emotion:   [solemn] [grave] [sorrowful] [defiant] [furious] [reverent] [bitter] [resolute] [whispering] [intense] [contemplative]
Delivery:  [slowly] [softly] [building] [emphatic] [measured]
Pacing:    [pause]   (a deliberate beat of silence)

Use tags SPARINGLY — at most one tag per sentence, plus [pause] where the drama demands it. Over-tagging makes the voice unstable and fake. Silence and restraint are tools; a well-placed [pause] beats three adjectives.

═══ DRIVE FROM emotional_core ═══
- rage / defiance  → [defiant], [furious], [bitter], [emphatic]; clipped, forceful.
- sacrifice / grief → [solemn], [sorrowful], [reverent], [slowly]; heavy, deliberate.
- betrayal         → [bitter], [grave], [whispering]; cold, controlled.
- stillness / duty → [contemplative], [measured], [softly]; calm, certain.
Never let the performance contradict the emotional_core.

═══ EXAMPLE (annotate, do not rewrite) ═══
INPUT  (emotional_core: betrayal):
  "He was denied entry — for his birth, not his skill. The greatest archer of his generation was told the school wasn't for him."
OUTPUT:
  "[grave] He was denied entry — for his birth, [pause] not his skill. [bitter] The greatest archer of his generation [pause] was told the school wasn't for him."

Direct the performance. Then stay silent."""

def generate_voice_direction(api_key: str, script: str, emotion: str) -> VoiceDirectionOutput:
    client = genai.Client(api_key=api_key)
    prompt = f"emotional_core: {emotion}\n\nSCRIPT:\n{script}"
    
    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt,
        config=types.GenerateContentConfig(
            system_instruction=VOICE_DIRECTOR_SYSTEM_PROMPT,
            response_mime_type="application/json",
            response_schema=VoiceDirectionOutput,
            temperature=0.2
        )
    )
    
    if not response.text:
        raise ValueError("Empty response from Voice Director agent.")
    data = json.loads(response.text)
    
    # SAFETY FALLBACK: The Voice Director MUST NOT alter the word count.
    # If the LLM hallucinates and drops a word (e.g. the first name), the kinetic timeline will desync.
    import re
    clean_tagged = re.sub(r'\[.*?\]', '', data.get('tagged_script', '')).strip()
    
    # We compare word counts (rough but effective heuristic for dropped words)
    original_words = len(script.split())
    returned_words = len(clean_tagged.split())
    
    if original_words != returned_words:
        print(f"WARNING: Voice Director altered word count! (Expected {original_words}, got {returned_words}). Triggering safety fallback to original script.")
        data['tagged_script'] = script
        data['direction_notes'] = "FALLBACK TRIGGERED: LLM altered script length. " + data.get('direction_notes', '')
        
    return VoiceDirectionOutput(**data)
# ═══════════════════════════════════════════════════════════════
# SECTION 2D: AI VIDEO ENGINE (ELEVENLABS + MOVIEPY)
# ═══════════════════════════════════════════════════════════════

def generate_elevenlabs_voiceover(text: str, api_key: str, voice_id: str = "RXZGC6H41rpnXBWuHTQD", stability: float = 0.5, style: float = 0.0):
    """
    Calls ElevenLabs /with-timestamps API to get the audio and word-level timings.
    Returns (audio_bytes, alignment_data)
    """
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/with-timestamps"
    headers = {
        "xi-api-key": api_key,
        "Content-Type": "application/json"
    }
    payload = {
        "text": text,
        "model_id": "eleven_v3",
        "voice_settings": {
            "stability": stability,
            "similarity_boost": 0.75,
            "style": style
        }
    }
    r = requests.post(url, json=payload, headers=headers)
    if r.status_code != 200:
        raise Exception(f"ElevenLabs API Error: {r.text}")
    
    data = r.json()
    audio_base64 = data.get("audio_base64")
    audio_bytes = base64.b64decode(audio_base64)
    alignment = data.get("alignment", {})
    return audio_bytes, alignment


def group_characters_to_words(alignment):
    """Converts ElevenLabs character-level timings into word-level timings."""
    if not alignment or 'characters' not in alignment:
        return []
    
    words = []
    current_word = ''
    start_t = None
    end_t = None
    in_tag = False
    
    for i, char in enumerate(alignment['characters']):
        if char == '[':
            in_tag = True
            continue
        if char == ']':
            in_tag = False
            continue
            
        if in_tag:
            continue
            
        if char.strip() == '':
            if current_word:
                words.append({'word': current_word, 'start': start_t, 'end': end_t})
                current_word = ''
                start_t = None
        else:
            if current_word == '':
                start_t = alignment['character_start_times_seconds'][i]
            current_word += char
            end_t = alignment['character_end_times_seconds'][i]
            
    if current_word:
        words.append({'word': current_word, 'start': start_t, 'end': end_t})
        
    return words

def compile_video_reel(audio_bytes, words_data, frames_bytes, output_filename="reel.mp4", total_words=0):
    """
    Compiles the reel from audio and the 30fps perfectly timed frame PNGs.
    """
    from moviepy import ImageSequenceClip, AudioFileClip, AudioClip, CompositeAudioClip
    import tempfile
    import os
    import numpy as np
    
    fps = 30
    INTRO_DURATION = 0.5  # seconds
    
    # Save audio to temp
    fd_a, temp_audio = tempfile.mkstemp(suffix=".mp3")
    with open(temp_audio, "wb") as f:
        f.write(audio_bytes)
    os.close(fd_a)
    
    # Save frames to temp directory
    temp_dir = tempfile.mkdtemp()
    frame_files = []
    for i, frame_bytes in enumerate(frames_bytes):
        filepath = os.path.join(temp_dir, f"frame_{i:04d}.jpg")
        with open(filepath, "wb") as f:
            f.write(frame_bytes)
        frame_files.append(filepath)
        
    video = ImageSequenceClip(frame_files, fps=fps)
    audio = AudioFileClip(temp_audio)
    
    # Create silent intro + actual audio
    def make_silence(t):
        if isinstance(t, np.ndarray):
            return np.zeros((len(t), 2))
        return np.array([0.0, 0.0])
        
    silence = AudioClip(make_silence, duration=INTRO_DURATION, fps=44100)
    
    # Mix the silence + delayed audio
    offset_audio = CompositeAudioClip([silence, audio.with_start(INTRO_DURATION)])
    
    # We must ensure audio duration doesn't exceed video duration (or vice versa)
    video = video.with_audio(offset_audio)
    video.write_videofile(output_filename, fps=fps, codec="libx264", audio_codec="aac", ffmpeg_params=["-crf", "18", "-pix_fmt", "yuv420p", "-preset", "slow"])
    
    # Cleanup temp files (best effort)
    try:
        os.remove(temp_audio)
        for f in frame_files:
            os.remove(f)
        os.rmdir(temp_dir)
    except:
        pass
    
    return output_filename


def get_instagram_account_info(ig_user_id: str, access_token: str) -> dict:
    """Fetch the Instagram account username to verify credentials."""
    url = f"https://graph.facebook.com/v22.0/{ig_user_id}?fields=username,name&access_token={access_token}"
    r = requests.get(url)
    if r.status_code == 200:
        return {"success": True, "data": r.json()}
    else:
        return {"success": False, "error": r.text}

def find_instagram_id(access_token: str) -> dict:
    """Find the Instagram Business Account ID associated with the token."""
    url = f"https://graph.facebook.com/v22.0/me/accounts?fields=name,instagram_business_account&access_token={access_token}"
    r = requests.get(url)
    if r.status_code == 200:
        data = r.json().get("data", [])
        for page in data:
            if "instagram_business_account" in page:
                return {"success": True, "id": page["instagram_business_account"]["id"], "name": page.get("name", "Unknown Page")}
        return {"success": False, "error": "No linked Instagram Business Account found."}
    else:
        return {"success": False, "error": r.text}

# ═══════════════════════════════════════════════════════════════
# SECTION 3: SESSION STATE INITIALIZATION
# ═══════════════════════════════════════════════════════════════

if "app_initialized" not in st.session_state:
    st.session_state["app_initialized"] = True
    st.session_state["preview_html"] = None
    st.session_state["preview_type"] = None
    st.session_state["preview_dims"] = (1080, 1080)
    # Engine state
    st.session_state["engine_brief"] = None
    st.session_state["engine_draft"] = None
    st.session_state["engine_final"] = None
    st.session_state["engine_caption"] = None
    st.session_state["engine_user_input"] = ""
    st.session_state["engine_phase"] = None  # None, strategist, copywriter, editor, caption, done
    # Carousel navigation
    st.session_state["engine_carousel_slides"] = []
    st.session_state["engine_preview_slide"] = 1


# ═══════════════════════════════════════════════════════════════
# SECTION 4: CUSTOM CSS
# ═══════════════════════════════════════════════════════════════

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Cinzel+Decorative:wght@400;700;900&family=Cinzel:wght@400;600;700&family=EB+Garamond:ital,wght@0,400;0,500;0,600;1,400;1,500&display=swap');

.stApp { background-color: #0A0804; }
h1, h2, h3 { font-family: 'Cinzel', serif !important; color: #C9922A !important; }
.stMarkdown p { color: #EDE0C4; }
label { color: #F5D78E !important; font-family: 'Cinzel', serif !important; letter-spacing: 1px !important; }

[data-testid="stSidebar"] { background-color: #110E07 !important; border-right: 1px solid rgba(201,146,42,0.2); }
[data-testid="stSidebar"] h1, [data-testid="stSidebar"] h2, [data-testid="stSidebar"] h3 { color: #C9922A !important; }
[data-testid="stSidebar"] .stMarkdown p { color: #EDE0C4; }

.stButton > button, .stDownloadButton > button {
    background: linear-gradient(135deg, #C9922A, #7A3B10) !important;
    color: #F2E8D0 !important; border: 1px solid #C9922A !important;
    font-family: 'Cinzel', serif !important; letter-spacing: 2px !important;
    text-transform: uppercase !important; font-size: 12px !important;
    padding: 12px 32px !important; border-radius: 2px !important;
}
.stButton > button:hover, .stDownloadButton > button:hover {
    background: linear-gradient(135deg, #E8B84B, #C9922A) !important;
}

.stSelectbox > div > div, .stTextInput > div > div > input, .stTextArea > div > div > textarea, .stNumberInput > div > div > input {
    background-color: #110E07 !important; color: #EDE0C4 !important;
    border: 1px solid rgba(201,146,42,0.3) !important; font-family: 'EB Garamond', serif !important;
}

.stTabs [data-baseweb="tab-list"] { gap: 8px; }
.stTabs [data-baseweb="tab"] {
    background-color: #110E07 !important; color: #C9922A !important;
    border: 1px solid rgba(201,146,42,0.2) !important; font-family: 'Cinzel', serif !important;
    font-size: 11px !important; letter-spacing: 2px !important;
}
.stTabs [aria-selected="true"] {
    background-color: rgba(201,146,42,0.15) !important; border-bottom: 2px solid #C9922A !important;
}
hr { border-color: rgba(201,146,42,0.15) !important; }
[data-testid="stFileUploader"] { border: 1px dashed rgba(201,146,42,0.3) !important; border-radius: 4px; }
</style>
""", unsafe_allow_html=True)


# ═══════════════════════════════════════════════════════════════
# SECTION 5: HELPERS & TEMPLATE GENERATORS
# ═══════════════════════════════════════════════════════════════

def wrap_words(text, start_idx=0):
    if not text:
        return "", start_idx
    text = text.replace('<br>', ' <br> ')
    words = text.split()
    wrapped = []
    idx = start_idx
    for w in words:
        if w == '<br>':
            wrapped.append(w)
        else:
            clean_w = ''.join(c for c in w if c.isalnum())
            if clean_w: # It has speakable characters
                wrapped.append(f'<span class="word" id="word-{idx}">{w}</span>')
                idx += 1
            else:
                wrapped.append(w) # pure punctuation like em-dashes, no span
    return " ".join(wrapped), idx

def get_video_frames(html_content, words_data, w=1080, h=1920, audio_duration=0.0):
    """Uses Playwright to snapshot JS-driven cinematic frames.
    
    Generates:
      - 30 fps continuous frames for perfectly smooth kinetic typography
    """
    from playwright.sync_api import sync_playwright
    import json
    
    frames = []
    fps = 30
    INTRO_DURATION = 0.5
    total_duration = INTRO_DURATION + audio_duration
    total_frames = int(total_duration * fps)
    
    words_data_json = json.dumps(words_data).replace("'", "\\'")
    
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={'width': w, 'height': h})
        page.set_content(html_content)
        page.wait_for_load_state('networkidle')
        page.evaluate("document.fonts.ready")
        import time
        time.sleep(1.5)
        
        # We need to map frame_idx to real time.
        # At frame_idx 0, t = -0.5 (start of intro)
        # At frame_idx (0.5 * fps), t = 0.0 (audio starts)
        
        for f in range(total_frames):
            t = (f / fps) - INTRO_DURATION
            # We call renderFrame with exact time t and audio_duration
            page.evaluate(f"renderFrame({t}, '{words_data_json}', {audio_duration})")
            frames.append(page.screenshot(type="jpeg", quality=90))
            
        browser.close()
    return frames

def image_to_base64(uploaded_file):
    if uploaded_file is not None:
        bytes_data = uploaded_file.getvalue()
        b64 = base64.b64encode(bytes_data).decode()
        mime = uploaded_file.type or "image/jpeg"
        return f"data:{mime};base64,{b64}"
    return None

@st.cache_data(show_spinner=True)
def get_image_from_html(html_content, width=1080, height=1080):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={'width': width, 'height': height})
        page.set_content(html_content)
        page.wait_for_load_state('networkidle')
        page.evaluate("document.fonts.ready")
        import time
        time.sleep(1.5)
        img_bytes = page.screenshot(type="png")
        browser.close()
        return img_bytes

FORMATS = {
    "1:1 Feed Post (1080x1080)": (1080, 1080),
    "9:16 Reel (1080x1920)": (1080, 1920),
}


def get_brand_css(w, h, is_reel=False, emotion="default"):
    # Auto-detect reel from aspect ratio (9:16 = h > w * 1.5)
    is_reel = is_reel or (h > w * 1.4)
    
    pad_quote = "200px 80px" if is_reel else "80px"
    pad_slide = "180px 80px 200px 80px" if is_reel else "60px 50px 100px 50px"
    pad_reflect = "200px 80px 180px 80px" if is_reel else "70px"
    pad_list = "180px 80px" if is_reel else "70px"
    pad_cover = "200px 80px" if is_reel else "80px 70px"
    qs_mt = "52px" if is_reel else "28px"
    corner_size = "120px" if is_reel else "80px"
    corner_inset = "52px" if is_reel else "32px"
    reflect_title_size = "38px" if is_reel else "32px"
    reflect_title_render = "72px" if is_reel else "var(--title-size)"
    reflect_body_size = "40px" if is_reel else "28px"
    reflect_bottom_pad = "180px" if is_reel else "70px"
    
    # Reel-specific overrides for other card types
    quote_font_size = "38px" if is_reel else "28px"
    quote_source_size = "28px" if is_reel else "22px"
    slide_title_size = "48px" if is_reel else "var(--title-size)"
    slide_body_size = "30px" if is_reel else "24px"
    brand_size = "16px" if is_reel else "14px"
    brand_spacing = "7px" if is_reel else "5px"

    # Phase 2: Emotion Mapping Lookup Table
    emotion_lower = str(emotion).lower()
    if "rage" in emotion_lower or "defiance" in emotion_lower:
        palette = {"gold": "#C9922A", "gold2": "#7A3B10", "gold3": "#4A1805", "cream": "#F2E8D0", "black": "#120502", "deep": "#180A04"}
    elif "stillness" in emotion_lower or "duty" in emotion_lower:
        palette = {"gold": "#A09A8F", "gold2": "#6D6860", "gold3": "#3A3630", "cream": "#D0CCC4", "black": "#04060A", "deep": "#080A10"}
    elif "sacrifice" in emotion_lower or "betrayal" in emotion_lower:
        palette = {"gold": "#D4AF37", "gold2": "#B59410", "gold3": "#8B7500", "cream": "#FFF8DC", "black": "#000000", "deep": "#000000"}
    else:
        palette = {"gold": "#C9922A", "gold2": "#E8B84B", "gold3": "#F5D78E", "cream": "#F2E8D0", "black": "#0A0804", "deep": "#110E07"}

    return f"""
@import url('https://fonts.googleapis.com/css2?family=Cinzel+Decorative:wght@400;700;900&family=Cinzel:wght@400;600;700&family=EB+Garamond:ital,wght@0,400;0,500;0,600;1,400;1,500&display=swap');
:root {{
    --gold: {palette['gold']}; --gold2: {palette['gold2']}; --gold3: {palette['gold3']};
    --cream: {palette['cream']}; --black: {palette['black']}; --deep: {palette['deep']};
    --text: #EDE0C4;
    
    --pad-quote: {pad_quote};
    --pad-slide: {pad_slide};
    --pad-reflect: {pad_reflect};
    --pad-list: {pad_list};
    --pad-cover: {pad_cover};
    
    --corner-size: {corner_size};
    --corner-inset: {corner_inset};
    --title-size: {reflect_title_size};
}}
*, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{ background: var(--black); font-family: 'EB Garamond', serif; overflow: hidden; margin: 0; width: {w}px; height: {h}px; }}
.card {{ width: {w}px; height: {h}px; position: relative; overflow: hidden; }}

/* Phase 2: Duotone Backgrounds & Vignette */
.battle-bg {{ position: absolute; inset: 0; pointer-events: none; background-size: cover; background-position: center; mix-blend-mode: overlay; opacity: 0.8; filter: url(#duotone); }}
.vignette-overlay {{ position: absolute; inset: 0; pointer-events: none; background: radial-gradient(circle, transparent 30%, var(--black) 100%); z-index: 1; }}

/* Phase 1 & 3: Componentized HTML */
.cq-corner {{ position: absolute; width: var(--corner-size); height: var(--corner-size); z-index: 2; }}
.cq-corner.tl {{ top: var(--corner-inset); left: var(--corner-inset); border-top: 2px solid var(--gold); border-left: 2px solid var(--gold); }}
.cq-corner.tr {{ top: var(--corner-inset); right: var(--corner-inset); border-top: 2px solid var(--gold); border-right: 2px solid var(--gold); }}
.cq-corner.bl {{ bottom: var(--corner-inset); left: var(--corner-inset); border-bottom: 2px solid var(--gold); border-left: 2px solid var(--gold); }}
.cq-corner.br {{ bottom: var(--corner-inset); right: var(--corner-inset); border-bottom: 2px solid var(--gold); border-right: 2px solid var(--gold); }}

.brand {{ font-family: 'Cinzel', serif; font-size: {brand_size}; letter-spacing: {brand_spacing}; text-transform: uppercase; color: var(--gold); opacity: 0.6; z-index: 3; position: relative; }}
.brand-footer {{ position: absolute; bottom: 36px; left: 50%; transform: translateX(-50%); width: 100%; text-align: center; }}

/* SVG Divider (Phase 3) */
.svg-divider {{ margin: 24px 0; opacity: 0.8; z-index: 2; position: relative; text-align: center; }}

/* Typography Baseline (Phase 3) */
.qt, .stitle, .rtitle, .ltitle, .ctitle {{
    font-family: 'Cinzel Decorative', serif; 
    font-weight: 700;
    color: var(--cream);
    text-transform: uppercase;
    position: relative;
    z-index: 2;
}}

/* Texture and Metallic Gradients (Phase 2) */
.gold-text {{
    background: linear-gradient(135deg, var(--gold3) 0%, var(--gold) 50%, var(--gold2) 100%);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    background-clip: text;
    filter: url(#grain);
}}

/* Specific Cards */
.card-quote {{ background: var(--black); display: flex; flex-direction: column; align-items: center; justify-content: center; padding: var(--pad-quote); text-align: center; }}
.card-quote .qt {{ font-size: {quote_font_size}; line-height: 1.75; letter-spacing: .5px; margin-bottom: {qs_mt}; }}
.card-quote .qs {{ font-family: 'EB Garamond', serif; font-size: {quote_source_size}; font-style: italic; color: var(--gold); opacity: .85; z-index: 2; }}

.card-slide {{ background: var(--deep); display: flex; flex-direction: column; justify-content: space-between; padding: var(--pad-slide); }}
.card-slide .snum {{ position: absolute; right: 50px; top: 30px; font-size: 140px; color: var(--gold); opacity: 0.1; line-height: 1; pointer-events: none; font-family: 'Cinzel Decorative', serif; font-weight: 900; }}
.card-slide .stitle {{ font-size: {slide_title_size}; line-height: 1.35; max-height: 350px; overflow: hidden; }}
.card-slide .sbody {{ font-size: {slide_body_size}; line-height: 1.8; color: var(--text); opacity: 0.9; margin-top: 24px; z-index: 2; position: relative; }}
.progress-bar-container {{ position: absolute; bottom: 0; left: 0; height: 6px; width: 100%; background: rgba(0,0,0,0.5); z-index: 5; }}
.progress-bar-fill {{ height: 100%; background: linear-gradient(90deg, var(--gold2), var(--gold3)); transition: width 0.3s; }}

/* Character Card */
.card-char {{ display:flex;flex-direction:column;justify-content:flex-end;padding-bottom:240px !important;padding-left:80px !important; }}
.cchar-bg {{ position:absolute;top:40px;left:0;right:0;text-align:center;font-family:'Cinzel Decorative',serif;font-weight:900;color:rgba(201,146,42,0.15);line-height:1;pointer-events:none;white-space:nowrap;z-index:5;letter-spacing:12px; }}
.cchar-name {{ font-size:88px !important;font-weight:900;line-height:0.9;margin-bottom:15px;font-family:'Cinzel Decorative',serif;background:linear-gradient(135deg,var(--gold3),var(--gold));-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text;filter:drop-shadow(0 4px 15px rgba(0,0,0,0.8)); }}
.cchar-traits {{ display:flex;flex-wrap:wrap;gap:15px;margin-bottom:40px; }}
.cchar-quote {{ font-size:34px !important;line-height:1.5;color:var(--cream);font-weight:600;border-left:4px solid var(--gold);padding-left:25px;max-width:95%;font-style:italic;text-shadow:0 4px 15px rgba(0,0,0,0.9);z-index:10;position:relative; }}

/* Reflection */
.card-reflection {{ background: var(--black); display: flex; flex-direction: column; justify-content: center; padding: var(--pad-reflect); text-align: left; }}
.card-reflection .rtitle {{ font-size: {reflect_title_render}; margin-bottom: 36px; line-height: 1.15; }}
.card-reflection .rbody {{ font-size: {reflect_body_size}; line-height: 1.65; color: var(--text); font-weight: 500; position: relative; z-index: 2; }}
.card-reflection .svg-divider {{ margin: 36px 0; }}

/* KINETIC TYPOGRAPHY ANIMATION (Phase 5 — cinematic motion) */
.word {{ display: inline-block; opacity: 0; }}
.word.revealed {{ opacity: 1; }}
.word.highlight {{ opacity: 1; color: var(--gold3); text-shadow: 0 0 4px var(--gold); }}

/* Entrance classes for reel motion */
.entrance {{ opacity: 0; transform: translateY(20px); }}
.entrance.entered {{ opacity: 1; transform: translateY(0); }}
.battle-bg {{ }}
"""

def html_brand_js():
    return """<script>
    /* Phase 7: Frame-by-Frame Continuous Rendering Engine */
    function renderFrame(t, wordsDataStr, totalDuration) {
        var words = document.querySelectorAll('.word');
        var wordsData = JSON.parse(wordsDataStr);
        
        words.forEach(function(e, i) {
            if (!wordsData[i]) return;
            var w = wordsData[i];
            
            var opacity = 0;
            var scale = 1.0;
            var isHighlight = false;
            var fadeTime = 0.15;
            
            if (t < w.start - fadeTime) {
                opacity = 0;
                scale = 1.0;
            } else if (t >= w.start - fadeTime && t < w.start) {
                var p = (t - (w.start - fadeTime)) / fadeTime;
                opacity = p;
                scale = 1.0 + (0.1 * p); // smoothly zoom to 1.1
            } else if (t >= w.start && t <= w.end) {
                opacity = 1;
                scale = 1.1;
                isHighlight = true;
            } else if (t > w.end && t <= w.end + fadeTime) {
                var p = (t - w.end) / fadeTime;
                opacity = 1;
                scale = 1.1 - (0.1 * p); // smoothly zoom down to 1.0
            } else {
                opacity = 1;
                scale = 1.0;
            }
            
            e.style.opacity = opacity;
            e.style.transform = "scale(" + scale + ")";
            
            if (isHighlight) {
                e.classList.add('highlight');
            } else {
                e.classList.remove('highlight');
            }
        });


        /* Ken Burns on background */
        var bg = document.querySelector('.battle-bg');
        if (bg && totalDuration > 0) {
            // Intro starts at t=-0.5, audio ends at t=totalDuration
            var progress = Math.max(0, t + 0.5) / (totalDuration + 0.5);
            var scale = 1.0 + (progress * 0.15);
            var tx = progress * -20;
            var ty = progress * -10;
            bg.style.transform = 'scale(' + scale + ') translate(' + tx + 'px, ' + ty + 'px)';
        }

        /* Vignette subtle radial shift for no-bg cards */
        var vig = document.querySelector('.vignette-overlay');
        if (vig && totalDuration > 0) {
            var p2 = Math.max(0, t + 0.5) / (totalDuration + 0.5);
            vig.style.background = 'radial-gradient(circle at ' + (50 + p2 * 5) + '% ' + (50 - p2 * 3) + '%, transparent 30%, var(--black) 100%)';
        }

        /* Staggered entrance for UI elements */
        var entrances = [
            {sel: '.brand', time: -0.4},
            {sel: '.cq-corner.tl', time: -0.3},
            {sel: '.cq-corner.tr', time: -0.3},
            {sel: '.cq-corner.bl', time: -0.2},
            {sel: '.cq-corner.br', time: -0.2},
            {sel: '.svg-divider', time: -0.1},
            {sel: '.brand-footer', time: 0.0}
        ];
        entrances.forEach(function(e) {
            var el = document.querySelector(e.sel);
            if (el) {
                if (t >= e.time) { el.classList.add('entered'); } else { el.classList.remove('entered'); }
            }
        });
        
        /* Progress Bar (if exists) */
        var pb = document.querySelector('.progress-bar-fill');
        if (pb && totalDuration > 0) {
            var prog = Math.max(0, t) / totalDuration;
            pb.style.width = (prog * 100) + '%';
        }
    }

    /* Backward compat: old setHighlight still works for static preview */
    function setHighlight(idx) {
        document.querySelectorAll('.word').forEach(function(e) { e.classList.remove('highlight'); });
        if (idx >= 0) {
            var el = document.getElementById('word-' + idx);
            if(el) el.classList.add('highlight');
        }
    }

    /* Auto-fit Title */
    function autoFitTitles() {
        var titles = document.querySelectorAll('.stitle, .rtitle, .qt');
        titles.forEach(function(el) {
            var fSize = parseInt(window.getComputedStyle(el).fontSize);
            while(el.scrollHeight > el.clientHeight && fSize > 12) {
                fSize -= 1;
                el.style.fontSize = fSize + 'px';
            }
        });
    }

    /* Static path: reveal all words + show all entrances on load */
    document.addEventListener("DOMContentLoaded", function() {
        autoFitTitles();
        document.querySelectorAll('.word').forEach(function(e) { e.classList.add('revealed'); });
        document.querySelectorAll('.entrance').forEach(function(e) { e.classList.add('entered'); });
    });
</script>"""

# HTML HELPERS (Phase 1 & 2 & 3)
def html_svg_defs():
    return """
    <svg width="0" height="0" style="position:absolute;z-index:-1;">
        <defs>
            <filter id="duotone">
                <feColorMatrix type="matrix" values="
                    0.3 0.3 0.3 0 0
                    0.3 0.3 0.3 0 0
                    0.3 0.3 0.3 0 0
                    0   0   0   1 0" />
                <feComponentTransfer>
                    <feFuncR type="linear" slope="1" intercept="0.1"/>
                    <feFuncG type="linear" slope="0.8" intercept="0"/>
                    <feFuncB type="linear" slope="0.2" intercept="0"/>
                </feComponentTransfer>
            </filter>
            <filter id="grain">
                <feTurbulence type="fractalNoise" baseFrequency="0.8" numOctaves="3" result="noise"/>
                <feColorMatrix type="matrix" values="1 0 0 0 0  0 1 0 0 0  0 0 1 0 0  0 0 0 0.15 0" in="noise" result="coloredNoise"/>
                <feComposite in="coloredNoise" in2="SourceGraphic" operator="in" result="compNoise"/>
                <feBlend in="SourceGraphic" in2="compNoise" mode="multiply"/>
            </filter>
        </defs>
    </svg>
    """

def html_corner_frames():
    return '<div class="cq-corner tl entrance"></div><div class="cq-corner tr entrance"></div><div class="cq-corner bl entrance"></div><div class="cq-corner br entrance"></div>'

def html_brand_footer():
    return '<div class="brand-footer entrance"><div class="brand">@TheMahabharataMindset</div></div>'

def html_bg(bg_b64):
    if not bg_b64: return '<div class="vignette-overlay"></div>'
    return f"""<div class="battle-bg" style="background-image:url('{bg_b64}');"></div><div class="vignette-overlay"></div>"""

def generate_quote_card(quote_text, source, bg_b64, sanskrit="", w=1080, h=1080, emotion="default"):
    css = get_brand_css(w, h, emotion=emotion)
    lines, idx = wrap_words(quote_text.replace('\\n', '<br>'), 0)
    source, idx = wrap_words(source, idx)
    sanskrit_html = f'<div class="sanskrit">{sanskrit}</div>' if sanskrit else ""
    return f"""<!DOCTYPE html><html><head><meta charset="UTF-8"><style>{css}</style>{html_brand_js()}</head><body>{html_svg_defs()}
<div class="card card-quote">{html_bg(bg_b64)}{html_corner_frames()}
  <div class="qt gold-text">{lines}</div><div class="qs">— {source}</div>{sanskrit_html}
  {html_brand_footer()}
</div></body></html>"""

def generate_carousel_slide(series_tag, slide_num, total_slides, title, body, bg_b64, w=1080, h=1080, emotion="default"):
    css = get_brand_css(w, h, emotion=emotion)
    title_html, idx = wrap_words(title, 0)
    if not title.strip().endswith(('.', '?', '!', '...')):
        title_html += '<span class="word" style="display:none;">...</span>'
        idx += 1
    body_html, idx = wrap_words(body, idx)
    slide_num_str = str(slide_num).zfill(2)
    progress_pct = (slide_num / total_slides) * 100
    return f"""<!DOCTYPE html><html><head><meta charset="UTF-8"><style>{css}</style>{html_brand_js()}</head><body>{html_svg_defs()}
<div class="card card-slide">{html_bg(bg_b64)}
  <div class="snum gold-text">{slide_num_str}</div>
  <div><div class="brand entrance" style="margin-bottom:12px;">{series_tag}</div><div class="stitle gold-text">{title_html}</div><div class="sbody">{body_html}</div></div>
  {html_brand_footer()}
  <div class="progress-bar-container"><div class="progress-bar-fill" style="width: {progress_pct}%;"></div></div>
</div></body></html>"""

def generate_character_spotlight(name, title, traits_list, quote, bg_b64, w=1080, h=1080, emotion="default"):
    css = get_brand_css(w, h, emotion=emotion)
    traits_html = "".join([f'<span style="background:var(--black);border:1px solid var(--gold);color:var(--gold3);padding:10px 22px;border-radius:2px;font-weight:700;letter-spacing:2px;display:inline-block;z-index:10;position:relative;">{t}</span>' for t in traits_list])
    ghost_size = min(220, int(w / (len(name.upper()) * 0.85)))
    
    name_html, idx = wrap_words(name, 0)
    if not name.strip().endswith(('.', '?', '!')):
        name_html += '<span class="word" style="display:none;">.</span>'
        idx += 1
        
    title_html, idx = wrap_words(title, idx)
    if not title.strip().endswith(('.', '?', '!')):
        title_html += '<span class="word" style="display:none;">.</span>'
        idx += 1
        
    quote_html, idx = wrap_words(quote, idx)
    
    return f"""<!DOCTYPE html><html><head><meta charset="UTF-8"><style>{css}</style>{html_brand_js()}</head><body>{html_svg_defs()}
<div class="card card-char">{html_bg(bg_b64)}
  <div class="cchar-bg" style="font-size:{ghost_size}px;">{name.upper()}</div>
  <div style="position:relative;z-index:10;max-width:82%;">
    <div class="brand entrance" style="margin-bottom:12px;">Character Spotlight</div>
    <div class="cchar-name gold-text">{name_html}</div><div class="stitle" style="font-size:22px;letter-spacing:3px;margin-bottom:35px;">{title_html}</div>
    <div class="cchar-traits">{traits_html}</div><div class="cchar-quote gold-text">"{quote_html}"</div>
  </div>
</div></body></html>"""

def generate_reflection(tag, title, body, bg_b64, w=1080, h=1080, emotion="default"):
    css = get_brand_css(w, h, emotion=emotion)
    title_html, idx = wrap_words(title.replace('\\n', '<br>'), 0)
    if not title.strip().endswith(('.', '?', '!', '...')):
        title_html += '<span class="word" style="display:none;">.</span>'
        idx += 1
    body_html, idx = wrap_words(body, idx)
    return f"""<!DOCTYPE html><html><head><meta charset="UTF-8"><style>{css}</style>{html_brand_js()}</head><body>{html_svg_defs()}
<div class="card card-reflection">{html_bg(bg_b64)}
  <div class="brand entrance" style="margin-bottom:28px;">{tag}</div>
  <div class="rtitle gold-text">{title_html}</div>
  <div class="svg-divider entrance"><svg width="60" height="8" viewBox="0 0 60 8" fill="none"><path d="M0 4L30 0L60 4L30 8L0 4Z" fill="var(--gold)"/></svg></div>
  <div class="rbody">{body_html}</div>
  {html_brand_footer()}
</div></body></html>"""

def generate_list_post(tag, title, items, bg_b64, w=1080, h=1080, emotion="default"):
    css = get_brand_css(w, h, emotion=emotion)
    items_html = "".join([f"""<div style="display:flex;gap:22px;margin-bottom:20px;z-index:2;position:relative;"><span class="stitle gold-text" style="font-size:28px;min-width:35px;">{i}</span><span style="font-family:'EB Garamond';font-size:24px;color:var(--text);line-height:1.6;">{item}</span></div>""" for i, item in enumerate(items, 1)])
    return f"""<!DOCTYPE html><html><head><meta charset="UTF-8"><style>{css}</style>{html_brand_js()}</head><body>{html_svg_defs()}
<div class="card card-slide">{html_bg(bg_b64)}
  <div><div class="brand entrance" style="margin-bottom:16px;">{tag}</div><div class="stitle gold-text" style="font-size:34px;margin-bottom:36px;">{title}</div></div>
  <div>{items_html}</div>
  {html_brand_footer()}
</div></body></html>"""

def generate_series_cover(series_label, number, title, subtitle, bg_b64, w=1080, h=1080, emotion="default"):
    css = get_brand_css(w, h, emotion=emotion)
    title_html = title.replace('\\n', '<br>')
    num_str = str(number).zfill(2)
    return f"""<!DOCTYPE html><html><head><meta charset="UTF-8"><style>{css}</style>{html_brand_js()}</head><body>{html_svg_defs()}
<div class="card card-quote">{html_bg(bg_b64)}
  <div style="position:absolute;inset:0;display:flex;align-items:center;justify-content:center;z-index:1;"><svg width="900" height="900" viewBox="0 0 300 300" fill="none" opacity=".12"><circle cx="150" cy="150" r="140" stroke="var(--gold)" stroke-width="1"/><circle cx="150" cy="150" r="110" stroke="var(--gold)" stroke-width="1"/><circle cx="150" cy="150" r="80" stroke="var(--gold)" stroke-width="1"/></svg></div>
  <div class="brand entrance" style="border:1px solid rgba(201,146,42,.28);padding:8px 22px;margin-bottom:16px;z-index:2;">{series_label}</div>
  <div class="stitle gold-text" style="font-size:160px;line-height:1;margin:16px 0;">{num_str}</div>
  <div class="stitle gold-text" style="font-size:34px;letter-spacing:3px;">{title_html}</div>
  <div style="margin-top:22px;font-family:'EB Garamond';font-size:22px;font-style:italic;color:rgba(242,232,208,.38);z-index:2;position:relative;">{subtitle}</div>
  {html_brand_footer()}
</div></body></html>"""

def render_from_final(data, bg_b64, card_w, card_h, emotion="default"):
    t_type = data.get("template_type", "")
    if t_type == "Quote Card":
        return generate_quote_card(data.get("quote_text") or "", data.get("quote_source") or "", bg_b64, data.get("quote_sanskrit") or "", card_w, card_h, emotion=emotion)
    elif t_type == "Carousel Slide":
        slides = data.get("carousel_slides") or []
        if slides:
            s = slides[0]
            sl = s if isinstance(s, dict) else s.__dict__
            return generate_carousel_slide(data.get("carousel_tag") or "", sl.get("slide_num", 1), len(slides), sl.get("title", ""), sl.get("body", ""), bg_b64, card_w, card_h, emotion=emotion)
    elif t_type == "Character Spotlight":
        return generate_character_spotlight(data.get("char_name") or "", data.get("char_title") or "", data.get("char_traits") or [], data.get("char_quote") or "", bg_b64, card_w, card_h, emotion=emotion)
    elif t_type == "Reflection Post":
        return generate_reflection(data.get("reflection_tag") or "", data.get("reflection_title") or "", data.get("reflection_body") or "", bg_b64, card_w, card_h, emotion=emotion)
    elif t_type == "List / Tips":
        return generate_list_post(data.get("list_tag") or "", data.get("list_title") or "", data.get("list_items") or [], bg_b64, card_w, card_h, emotion=emotion)
    elif t_type == "Series Cover":
        return generate_series_cover(data.get("cover_label") or "", data.get("cover_number") or 1, data.get("cover_title") or "", data.get("cover_subtitle") or "", bg_b64, card_w, card_h, emotion=emotion)
    return None



# ═══════════════════════════════════════════════════════════════
# SECTION 6: APP LAYOUT
# ═══════════════════════════════════════════════════════════════

st.markdown("""
<div style="text-align:center; padding: 40px 0 20px;">
  <div style="font-family:'Cinzel',serif; font-size:10px; letter-spacing:8px; color:rgba(201,146,42,0.4); text-transform:uppercase; margin-bottom:20px;">⚔ Post Generator ⚔</div>
  <div style="font-family:'Cinzel Decorative',serif; font-size:36px; font-weight:700; background:linear-gradient(135deg,#F5D78E,#C9922A); -webkit-background-clip:text; -webkit-text-fill-color:transparent; letter-spacing:3px;">The Mahabharata Mindset</div>
  <div style="font-family:'EB Garamond',serif; font-size:16px; font-style:italic; color:rgba(242,232,208,0.35); margin-top:10px;">Create brand-perfect Instagram posts from your templates</div>
</div>
<hr style="border-color: rgba(201,146,42,0.15); margin: 10px 60px 30px;">
""", unsafe_allow_html=True)

# ─── Sidebar ───
with st.sidebar:
    st.markdown("### ⚔ Background Image")
    st.markdown('<p style="font-size:13px; color:rgba(242,232,208,0.5);">Upload a background image. It will be applied at low opacity.</p>', unsafe_allow_html=True)
    bg_file = st.file_uploader("Upload Background", type=["jpg", "jpeg", "png", "webp"], label_visibility="collapsed")
    bg_b64 = image_to_base64(bg_file) if bg_file else None
    if bg_file:
        st.image(bg_file, caption="Background Preview", use_column_width=True)
    else:
        st.markdown('<p style="font-size:12px; color:rgba(201,146,42,0.4); font-style:italic;">No background — solid dark with ember glow.</p>', unsafe_allow_html=True)

    st.markdown("---")
    st.markdown("### ☁️ Image Hosting")
    hosting_choice = st.radio("Upload images via:", ["ImgBB (Free & Instant)", "Google Drive (Requires Workspace)"], index=0, key="sidebar_hosting_choice")
    gdrive_folder = st.text_input("GDrive Folder ID", value=st.secrets.get("GDRIVE_FOLDER_ID", "1L1azA7lpLuVESbc_vFJ5x4-wNySxU24t"), key="sidebar_gdrive_folder_id")

    st.markdown("---")
    st.markdown("### 🔑 API Keys & Auth")
    gemini_key_input = st.text_input("Gemini API Key (or use env)", value=st.secrets.get("GEMINI_API_KEY", ""), type="password", key="sidebar_gemini_key")
    elevenlabs_key_input = st.text_input("ElevenLabs API Key", value=st.secrets.get("ELEVENLABS_API_KEY", ""), type="password", key="sidebar_elevenlabs_key")
    elevenlabs_voice_input = st.text_input("ElevenLabs Voice ID", value=st.secrets.get("ELEVENLABS_VOICE_ID", "RXZGC6H41rpnXBWuHTQD"), help="Default is user-preferred Indian voice", key="sidebar_elevenlabs_voice_id")
    ig_user_id = st.text_input("Instagram User ID", value=st.secrets.get("IG_USER_ID", ""), key="sidebar_ig_user_id")
    ig_token = st.text_input("Instagram Access Token", value=st.secrets.get("IG_ACCESS_TOKEN", ""), type="password", key="sidebar_ig_token")
    
    if st.button("Verify IG Account"):
        if not ig_user_id or not ig_token:
            st.error("Enter both User ID and Token to verify.")
        else:
            info = get_instagram_account_info(ig_user_id, ig_token)
            if info["success"]:
                username = info["data"].get("username", "Unknown")
                name = info["data"].get("name", "Unknown")
                st.success(f"✅ Connected to: **@{username}** ({name})")
            else:
                st.error("❌ Verification Failed. Check credentials.")
                with st.expander("Show Error Details"):
                    st.code(info["error"])
                    
    if st.button("Lost IG ID? Find it here 🔍"):
        if not ig_token:
            st.error("Enter your Instagram Access Token first.")
        else:
            info = find_instagram_id(ig_token)
            if info["success"]:
                st.info(f"🎯 **Found it!**\n\nYour Instagram ID is:\n`{info['id']}`\n\n*(Linked to Facebook Page: {info['name']})*")
            else:
                st.error("❌ Could not find an Instagram ID.")
                with st.expander("Show Error Details"):
                    st.code(info["error"])

    st.markdown("---")
    st.markdown("### 📐 Post Format")
    fmt_choice = st.radio("Aspect Ratio", list(FORMATS.keys()), index=0, key="sidebar_fmt_choice", help="1:1 for feed, 9:16 for Reels")
    card_w, card_h = FORMATS[fmt_choice]
    st.markdown(f'<p style="font-size:11px; color:rgba(201,146,42,0.35); font-style:italic;">{card_w}x{card_h}px</p>', unsafe_allow_html=True)

    st.markdown("---")
    st.markdown("### 🎨 Brand Colors")
    st.markdown("""<div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:8px;">
      <div style="width:40px;height:40px;background:#0A0804;border:1px solid rgba(201,146,42,0.3);border-radius:2px;" title="#0A0804"></div>
      <div style="width:40px;height:40px;background:#110E07;border:1px solid rgba(201,146,42,0.3);border-radius:2px;" title="#110E07"></div>
      <div style="width:40px;height:40px;background:#C9922A;border-radius:2px;" title="#C9922A"></div>
      <div style="width:40px;height:40px;background:#E8B84B;border-radius:2px;" title="#E8B84B"></div>
      <div style="width:40px;height:40px;background:#F5D78E;border-radius:2px;" title="#F5D78E"></div>
      <div style="width:40px;height:40px;background:#F2E8D0;border-radius:2px;" title="#F2E8D0"></div>
      <div style="width:40px;height:40px;background:#7A3B10;border-radius:2px;" title="#7A3B10"></div>
    </div>""", unsafe_allow_html=True)


preview_h = int(620 * card_h / card_w)

# ─── Tabs ───
tab_engine, tab_archive, tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs([
    "⚔️ CONTENT ENGINE", "📜 ARCHIVE", "❝ QUOTE CARD", "📄 CAROUSEL SLIDE", "⚔ CHARACTER", "🌙 REFLECTION", "📋 LIST / TIPS", "🎬 SERIES COVER"
])

# ═══════════════════════════════════════════════════════════════
# TAB 0: CONTENT ENGINE (3-Agent Pipeline)
# ═══════════════════════════════════════════════════════════════
with tab_engine:
    st.markdown("### ⚔️ Dharmic Content Engine")
    st.markdown("""<p style="font-size:14px; color:rgba(242,232,208,0.55);">
    Three specialized agents work in sequence to produce on-brand content:<br>
    <span style="color:#C9922A;">① Brand Strategist</span> → selects epic character, story moment, emotional core<br>
    <span style="color:#C9922A;">② Dharmic Copywriter</span> → writes copy calibrated to your brand voice<br>
    <span style="color:#C9922A;">③ Adversarial Editor</span> → runs the Arjuna Test, Buzzword Purge, Cadence Check, Brevity Blade
    </p>""", unsafe_allow_html=True)

    user_input = st.text_area(
        "Your Topic / Struggle / Brain-Dump",
        placeholder="E.g., Being overlooked for a promotion despite outperforming everyone.",
        height=120,
        key="engine_input_widget"
    )

    col_btn1, col_btn2 = st.columns([1, 1])
    with col_btn1:
        forge_clicked = st.button("Forge Content ⚔️", key="forge_engine_btn")
    with col_btn2:
        regen_clicked = st.button("Regenerate Copy Only 🔄", key="regen_engine_btn", disabled=not st.session_state.get("engine_brief"))

    # ── Run Full Pipeline ──
    if forge_clicked:
        if not user_input.strip():
            st.error("Enter a topic or struggle first.")
        else:
            api_key = st.session_state.get("sidebar_gemini_key") or os.environ.get("GEMINI_API_KEY")
            if not api_key:
                st.error("Gemini API Key missing. Enter it in the sidebar.")
            else:
                st.session_state["engine_user_input"] = user_input

                with st.status("⚔️ Forging content through the 3-agent pipeline...", expanded=True) as status:
                    try:
                        st.write("**① Brand Strategist** — analyzing angle...")
                        brief, draft, final, caption_data = run_full_pipeline(api_key, user_input)
                        
                        st.session_state["engine_brief"] = brief
                        st.write(f"✅ Strategist selected: **{brief['epic_character']}** — *{brief['story_moment'][:80]}...*")

                        st.session_state["engine_draft"] = draft
                        st.write(f"✅ Copywriter produced: **{draft['template_type']}**")

                        st.session_state["engine_final"] = final
                        approval = "✅ APPROVED" if final.get("approved") else "🔄 REWRITTEN"
                        st.write(f"✅ Editor verdict: **{approval}**")
                        
                        st.session_state["engine_caption"] = caption_data
                        st.write(f"✅ Caption & Hashtags generated")

                        # Store carousel slides for navigation
                        if final.get("template_type") == "Carousel Slide" and final.get("carousel_slides"):
                            st.session_state["engine_carousel_slides"] = final["carousel_slides"]
                            st.session_state["engine_preview_slide"] = 1
                        else:
                            st.session_state["engine_carousel_slides"] = []

                        # Save to archive
                        save_to_archive(user_input, brief, final, caption_data)

                        status.update(label="⚔️ Content forged successfully!", state="complete")
                    except Exception as e:
                        status.update(label="❌ Pipeline failed", state="error")
                        st.error(f"Error: {str(e)}")

    # ── Regenerate Copy Only ──
    if regen_clicked and st.session_state.get("engine_brief"):
        api_key = st.session_state.get("sidebar_gemini_key") or os.environ.get("GEMINI_API_KEY")
        if api_key:
            with st.status("🔄 Regenerating copy with same strategy...", expanded=True) as status:
                try:
                    brief = st.session_state["engine_brief"]
                    ui = st.session_state.get("engine_user_input", "")
                    draft, final, caption_data = run_copy_only(api_key, brief, ui)
                    
                    st.session_state["engine_draft"] = draft
                    st.session_state["engine_final"] = final
                    st.session_state["engine_caption"] = caption_data
                    
                    if final.get("template_type") == "Carousel Slide" and final.get("carousel_slides"):
                        st.session_state["engine_carousel_slides"] = final["carousel_slides"]
                        st.session_state["engine_preview_slide"] = 1
                    else:
                        st.session_state["engine_carousel_slides"] = []
                    # Save to archive
                    save_to_archive(st.session_state.get("engine_user_input", ""), brief, final, caption_data)

                    status.update(label="🔄 Copy regenerated!", state="complete")
                except Exception as e:
                    status.update(label="❌ Regeneration failed", state="error")
                    st.error(f"Error: {str(e)}")

    # ── Display Results ──
    if st.session_state.get("engine_final"):
        st.markdown("---")
        brief = st.session_state["engine_brief"]
        final = st.session_state["engine_final"]
        draft = st.session_state.get("engine_draft", {})

        col_info, col_preview = st.columns([1, 1])

        with col_info:
            # Strategist Brief Card
            st.markdown(f"""<div style="border:1px solid rgba(201,146,42,0.25);padding:20px;border-radius:4px;margin-bottom:16px;background:rgba(17,14,7,0.6);">
            <p style="font-family:'Cinzel',serif;font-size:11px;letter-spacing:4px;color:#C9922A;text-transform:uppercase;margin-bottom:12px;">① Strategist Brief</p>
            <p style="color:#F5D78E;font-size:16px;margin-bottom:8px;"><strong>{brief['epic_character']}</strong> — <em>{brief['emotional_core']}</em></p>
            <p style="color:#EDE0C4;font-size:14px;margin-bottom:6px;">📜 {brief['story_moment']}</p>
            <p style="color:rgba(237,224,196,0.6);font-size:13px;">🏢 {brief['modern_parallel']}</p>
            <p style="color:rgba(201,146,42,0.5);font-size:12px;margin-top:8px;">Template: {brief['recommended_template']} · Format: {brief['recommended_format']}</p>
            </div>""", unsafe_allow_html=True)

            # Editor Audit
            approved_icon = "✅" if final.get("approved") else "🔄"
            with st.expander(f"③ Adversarial Editor Audit {approved_icon}", expanded=True):
                st.write(final.get("audit_log", "No audit log."))

            # Carousel Navigation
            if final.get("template_type") == "Carousel Slide" and st.session_state.get("engine_carousel_slides"):
                slides = st.session_state["engine_carousel_slides"]
                st.markdown("#### 📄 Carousel Navigation")
                preview_slide = st.number_input("Preview Slide", min_value=1, max_value=len(slides), value=1, key="engine_slide_nav")
                st.session_state["engine_preview_slide"] = preview_slide

            if st.button("New Strategy ⚔️", key="new_strategy_btn"):
                st.session_state["engine_brief"] = None
                st.session_state["engine_draft"] = None
                st.session_state["engine_final"] = None
                st.session_state["engine_carousel_slides"] = []
                st.rerun()

        with col_preview:
            st.markdown("#### Preview")

            # Determine dimensions from strategist recommendation
            if brief.get("recommended_format") == "9:16 Reel":
                render_w, render_h = 1080, 1920
            else:
                render_w, render_h = card_w, card_h
            render_preview_h = int(620 * render_h / render_w)

            # Generate HTML for preview
            if final.get("template_type") == "Carousel Slide" and st.session_state.get("engine_carousel_slides"):
                slides = st.session_state["engine_carousel_slides"]
                idx = st.session_state.get("engine_preview_slide", 1) - 1
                idx = max(0, min(idx, len(slides) - 1))
                s = slides[idx]
                sl = s if isinstance(s, dict) else s.__dict__
                html = generate_carousel_slide(
                    final.get("carousel_tag") or "", sl.get("slide_num", idx + 1),
                    len(slides), sl.get("title", ""), sl.get("body", ""),
                    bg_b64, render_w, render_h, emotion=brief.get("emotional_core", "default")
                )
            else:
                html = render_from_final(final, bg_b64, render_w, render_h, emotion=brief.get("emotional_core", "default"))

            if html:
                st.components.v1.html(html, height=render_preview_h, scrolling=False)

                c1, c2 = st.columns(2)
                with c1:
                    st.download_button("⬇ HTML", html, file_name="tmm_post.html", mime="text/html", key="dl_html_engine")
                with c2:
                    png_bytes = get_image_from_html(html, render_w, render_h)
                    st.download_button("📸 PNG", png_bytes, file_name="tmm_post.png", mime="image/png", key="dl_png_engine")

            # CAPTION & PUBLISH SECTION
            caption_data = st.session_state.get("engine_caption")
            if caption_data:
                st.markdown("---")
                st.markdown("#### 📝 Instagram Caption")
                
                # Combine caption and hashtags for the text area
                raw_caption = caption_data.get("caption", "")
                hashtags = " ".join(caption_data.get("hashtags", []))
                full_caption = f"{raw_caption}\n\n{hashtags}"
                
                edited_caption = st.text_area("Edit Caption before publishing", value=full_caption, height=200, key="engine_edited_caption")
                
                ig_user_id = st.session_state.get("sidebar_ig_user_id")
                ig_token = st.session_state.get("sidebar_ig_token")
                elevenlabs_key = st.session_state.get("sidebar_elevenlabs_key")
                service_account_path = str(Path(__file__).parent / "the-mahabharata-mindset-1ddcb71bec5b.json")
                
                c_pub1, c_pub2 = st.columns(2)
                
                with c_pub1:
                    if st.button("📸 Publish Static Image", type="primary", key="publish_ig_btn"):
                        if not ig_user_id or not ig_token:
                            st.error("Instagram User ID and Access Token required in the sidebar.")
                        elif not Path(service_account_path).exists():
                            st.error("Google Drive service account JSON missing.")
                        else:
                            with st.status("Publishing to Instagram...", expanded=True) as pub_status:
                                try:
                                    st.write("📸 Rendering final image...")
                                    png_bytes = get_image_from_html(html, render_w, render_h)
                                    
                                    hosting_pref = st.session_state.get("sidebar_hosting_choice", "ImgBB")
                                    if "ImgBB" in hosting_pref:
                                        st.write("☁️ Uploading to ImgBB...")
                                        public_url = upload_to_imgbb(png_bytes)
                                    else:
                                        st.write("☁️ Uploading to Google Drive...")
                                        file_name = f"tmm_post_{int(time.time())}.png"
                                        public_url = upload_to_gdrive(png_bytes, file_name, service_account_path)
                                    
                                    if public_url.startswith("Error"):
                                        st.error(public_url)
                                        pub_status.update(label="❌ Publish Failed", state="error")
                                    else:
                                        st.write(f"✅ Uploaded to Cloud: [Link]({public_url})")
                                        st.write("📲 Sending to Instagram API...")
                                        result = publish_to_instagram_single(public_url, edited_caption, ig_user_id, ig_token, media_type="IMAGE")
                                        
                                        if result.startswith("Success"):
                                            st.success(result)
                                            pub_status.update(label="✅ Published Successfully!", state="complete")
                                        else:
                                            st.error(result)
                                            pub_status.update(label="❌ IG Publish Failed", state="error")
                                except Exception as e:
                                    pub_status.update(label="❌ Publish Failed", state="error")
                                    st.error(f"Error: {str(e)}")

                with c_pub2:
                    if st.button("🎬 Generate & Publish Reel", type="primary", key="publish_reel_btn"):
                        if not ig_user_id or not ig_token or not elevenlabs_key:
                            st.error("Instagram keys and ElevenLabs key required in the sidebar.")
                        else:
                            with st.status("Forging Reel Video...", expanded=True) as pub_status:
                                try:
                                    from bs4 import BeautifulSoup
                                    soup = BeautifulSoup(html, 'html.parser')
                                    spoken_words = [span.text for span in soup.find_all('span', class_='word')]
                                    text_to_read = " ".join(spoken_words)
                                    
                                    # Fallback if there are no spans (e.g. Series Cover)
                                    if not text_to_read.strip():
                                        if final.get("template_type") == "Quote Card":
                                            text_to_read = final.get("quote_text", "")
                                        elif final.get("template_type") == "Carousel Slide":
                                            s = slides[idx] if 'slides' in locals() else {}
                                            sl = s if isinstance(s, dict) else (s.__dict__ if hasattr(s, '__dict__') else {})
                                            text_to_read = f"{sl.get('title', '')} {sl.get('body', '')}"
                                        elif final.get("template_type") == "Character Spotlight":
                                            text_to_read = f"{final.get('char_name', '')}. {final.get('char_title', '')}. {final.get('char_quote', '')}"
                                        elif final.get("template_type") == "Reflection Post":
                                            text_to_read = f"{final.get('reflection_title', '')}. {final.get('reflection_body', '')}"
                                        else:
                                            text_to_read = "The Mahabharata Mindset"
                                        
                                    st.write("🎭 Consulting Voice Director...")
                                    emotion = brief.get("emotional_core", "default") if 'brief' in locals() and brief else "default"
                                    gemini_key = os.environ.get("GEMINI_API_KEY", "")
                                    vd_out = generate_voice_direction(gemini_key, text_to_read.replace('<br>','... '), emotion)
                                    
                                    st.info(f"**Voice Direction:** {vd_out.direction_notes}")
                                    st.write(f"🎙️ Requesting Voiceover (v3): *{vd_out.tagged_script}*")
                                    
                                    # We send the TAGGED script to ElevenLabs v3
                                    voice_id = st.session_state.get("sidebar_elevenlabs_voice_id", "RXZGC6H41rpnXBWuHTQD")
                                    audio_bytes, alignment = generate_elevenlabs_voiceover(
                                        vd_out.tagged_script, 
                                        elevenlabs_key, 
                                        voice_id,
                                        stability=vd_out.base_stability,
                                        style=vd_out.base_style
                                    )
                                    words_data = group_characters_to_words(alignment)
                                    
                                    import tempfile
                                    from moviepy import AudioFileClip
                                    fd_a, temp_audio = tempfile.mkstemp(suffix=".mp3")
                                    with open(temp_audio, "wb") as f:
                                        f.write(audio_bytes)
                                    os.close(fd_a)
                                    audio_clip = AudioFileClip(temp_audio)
                                    audio_duration = audio_clip.duration
                                    audio_clip.close()
                                    os.remove(temp_audio)
                                    
                                    st.write(f"🖼️ Rendering continuous 30fps frames (duration: {audio_duration}s)...")
                                    frames_bytes = get_video_frames(html, words_data, render_w, render_h, audio_duration=audio_duration)
                                    
                                    st.write("🎞️ Assembling Reel via MoviePy...")
                                    temp_video_path = f"tmm_reel_{int(time.time())}.mp4"
                                    compile_video_reel(audio_bytes, words_data, frames_bytes, temp_video_path, total_words=len(spoken_words))
                                    
                                    with open(temp_video_path, "rb") as f:
                                        st.download_button("⬇ Download Reel (.mp4)", f, file_name="tmm_reel.mp4", mime="video/mp4", key="dl_mp4_engine")
                                    
                                    st.write("☁️ Uploading to Video Host (Catbox)...")
                                    public_url = upload_to_catbox(temp_video_path)
                                    
                                    if public_url.startswith("Error") or not public_url.startswith("http"):
                                        st.error(public_url)
                                        pub_status.update(label="❌ Upload Failed", state="error")
                                    else:
                                        st.write(f"✅ Video Uploaded: [Link]({public_url})")
                                        st.write("📲 Sending to Instagram API as REELS...")
                                        result = publish_to_instagram_single(public_url, edited_caption, ig_user_id, ig_token, media_type="REELS")
                                        
                                        if result.startswith("Success"):
                                            st.success(result)
                                            pub_status.update(label="✅ Reel Published Successfully!", state="complete")
                                        else:
                                            st.error(result)
                                            pub_status.update(label="❌ IG Publish Failed", state="error")
                                            
                                        # Cleanup
                                        import os
                                        try:
                                            os.remove(temp_video_path)
                                        except: pass
                                except Exception as e:
                                    import traceback
                                    pub_status.update(label="❌ Generation Failed", state="error")
                                    st.error(f"Error: {str(e)}")
                                    st.code(traceback.format_exc())


# ═══════════════════════════════════════════════════════════════
# TAB ARCHIVE: Content Memory Dashboard
# ═══════════════════════════════════════════════════════════════
with tab_archive:
    st.markdown("### 📜 Content Archive")
    st.markdown('<p style="font-size:14px; color:rgba(242,232,208,0.55);">Every post forged by the Content Engine is stored here. The Strategist reads this archive before each generation to avoid repeating ideas.</p>', unsafe_allow_html=True)

    archive_data = load_archive()

    if not archive_data:
        st.markdown('<p style="color:rgba(201,146,42,0.5); font-style:italic;">No posts generated yet. Use the Content Engine to forge your first post.</p>', unsafe_allow_html=True)
    else:
        # ── Stats Row ──
        total_posts = len(archive_data)
        characters_used = list(set(e.get("brief", {}).get("epic_character", "?") for e in archive_data))
        templates_used = list(set(e.get("final", {}).get("template_type", "?") for e in archive_data))
        emotions_used = list(set(e.get("brief", {}).get("emotional_core", "?") for e in archive_data))

        stat1, stat2, stat3, stat4 = st.columns(4)
        with stat1:
            st.markdown(f'<div style="text-align:center;padding:16px;border:1px solid rgba(201,146,42,0.2);border-radius:4px;background:rgba(17,14,7,0.6);"><p style="font-family:\'Cinzel Decorative\',serif;font-size:36px;color:#C9922A;margin-bottom:4px;">{total_posts}</p><p style="font-family:\'Cinzel\',serif;font-size:11px;letter-spacing:3px;color:rgba(201,146,42,0.5);text-transform:uppercase;">Posts Forged</p></div>', unsafe_allow_html=True)
        with stat2:
            st.markdown(f'<div style="text-align:center;padding:16px;border:1px solid rgba(201,146,42,0.2);border-radius:4px;background:rgba(17,14,7,0.6);"><p style="font-family:\'Cinzel Decorative\',serif;font-size:36px;color:#C9922A;margin-bottom:4px;">{len(characters_used)}</p><p style="font-family:\'Cinzel\',serif;font-size:11px;letter-spacing:3px;color:rgba(201,146,42,0.5);text-transform:uppercase;">Characters</p></div>', unsafe_allow_html=True)
        with stat3:
            st.markdown(f'<div style="text-align:center;padding:16px;border:1px solid rgba(201,146,42,0.2);border-radius:4px;background:rgba(17,14,7,0.6);"><p style="font-family:\'Cinzel Decorative\',serif;font-size:36px;color:#C9922A;margin-bottom:4px;">{len(templates_used)}</p><p style="font-family:\'Cinzel\',serif;font-size:11px;letter-spacing:3px;color:rgba(201,146,42,0.5);text-transform:uppercase;">Templates</p></div>', unsafe_allow_html=True)
        with stat4:
            st.markdown(f'<div style="text-align:center;padding:16px;border:1px solid rgba(201,146,42,0.2);border-radius:4px;background:rgba(17,14,7,0.6);"><p style="font-family:\'Cinzel Decorative\',serif;font-size:36px;color:#C9922A;margin-bottom:4px;">{len(emotions_used)}</p><p style="font-family:\'Cinzel\',serif;font-size:11px;letter-spacing:3px;color:rgba(201,146,42,0.5);text-transform:uppercase;">Emotions</p></div>', unsafe_allow_html=True)

        st.markdown("---")

        # ── Filters ──
        filter_col1, filter_col2, filter_col3 = st.columns(3)
        with filter_col1:
            filter_char = st.selectbox("Filter by Character", ["All"] + sorted(characters_used), key="arch_filter_char")
        with filter_col2:
            filter_template = st.selectbox("Filter by Template", ["All"] + sorted(templates_used), key="arch_filter_tmpl")
        with filter_col3:
            filter_emotion = st.selectbox("Filter by Emotion", ["All"] + sorted(emotions_used), key="arch_filter_emo")

        # Apply filters
        filtered = archive_data
        if filter_char != "All":
            filtered = [e for e in filtered if e.get("brief", {}).get("epic_character") == filter_char]
        if filter_template != "All":
            filtered = [e for e in filtered if e.get("final", {}).get("template_type") == filter_template]
        if filter_emotion != "All":
            filtered = [e for e in filtered if e.get("brief", {}).get("emotional_core") == filter_emotion]

        st.markdown(f"**Showing {len(filtered)} of {total_posts} posts**")

        # ── Archive Cards (newest first) ──
        for entry in reversed(filtered):
            b = entry.get("brief", {})
            f = entry.get("final", {})
            title = extract_title_from_final(f)
            ts = entry.get("timestamp", "")[:16].replace("T", " · ")
            template_type = f.get("template_type", "?")
            character = b.get("epic_character", "?")
            emotion = b.get("emotional_core", "?")
            user_in = entry.get("user_input", "")[:100]
            entry_id = entry.get("id", "?")

            # Template icon mapping
            icon_map = {"Quote Card": "❝", "Carousel Slide": "📄", "Character Spotlight": "⚔", "Reflection Post": "🌙", "List / Tips": "📋", "Series Cover": "🎬"}
            icon = icon_map.get(template_type, "📌")

            st.markdown(f"""<div style="border:1px solid rgba(201,146,42,0.18);padding:20px;border-radius:4px;margin-bottom:12px;background:rgba(17,14,7,0.5);">
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;">
                <span style="font-family:'Cinzel',serif;font-size:11px;letter-spacing:3px;color:rgba(201,146,42,0.45);text-transform:uppercase;">#{entry_id} · {ts}</span>
                <span style="font-family:'Cinzel',serif;font-size:11px;letter-spacing:2px;color:#C9922A;text-transform:uppercase;border:1px solid rgba(201,146,42,0.25);padding:4px 12px;border-radius:2px;">{icon} {template_type}</span>
            </div>
            <p style="font-family:'Cinzel',serif;font-size:18px;color:#F5D78E;margin-bottom:8px;">{title}</p>
            <p style="font-size:13px;color:rgba(237,224,196,0.6);margin-bottom:6px;">⚔ {character} · <em>{emotion}</em> · {b.get('recommended_format', '?')}</p>
            <p style="font-size:12px;color:rgba(237,224,196,0.35);font-style:italic;">Input: "{user_in}"</p>
            </div>""", unsafe_allow_html=True)

            # Preview button
            with st.expander(f"Preview #{entry_id}", expanded=False):
                preview_final = entry.get("final", {})
                rformat = b.get("recommended_format", "1:1 Feed Post")
                if rformat == "9:16 Reel":
                    pw, ph = 1080, 1920
                else:
                    pw, ph = 1080, 1080
                ph_display = int(400 * ph / pw)
                preview_html = render_from_final(preview_final, bg_b64, pw, ph, emotion=b.get("emotional_core", "default"))
                if preview_html:
                    st.components.v1.html(preview_html, height=ph_display, scrolling=False)

        # ── Export / Clear ──
        st.markdown("---")
        exp_col1, exp_col2 = st.columns([1, 1])
        with exp_col1:
            st.download_button(
                "⬇ Export Archive (JSON)",
                json.dumps(archive_data, indent=2, ensure_ascii=False),
                file_name="tmm_archive.json",
                mime="application/json",
                key="dl_archive_json"
            )
        with exp_col2:
            if st.button("🗑️ Clear Archive", key="clear_archive_btn"):
                if ARCHIVE_PATH.exists():
                    ARCHIVE_PATH.unlink()
                st.rerun()


# ═══════════════════════════════════════════════════════════════
# MANUAL TEMPLATE TABS (Tab 1-6)
# ═══════════════════════════════════════════════════════════════

with tab1:
    col1, col2 = st.columns([1, 1])
    with col1:
        st.markdown("#### Quote Card")
        qt_text = st.text_area("Quote Text", value="You have the right to work,\nbut never to the fruit of the work.", height=120, key="qt_text_w")
        qt_source = st.text_input("Source", value="Bhagavad Gita 2.47", key="qt_source_w")
        qt_sanskrit = st.text_input("Sanskrit (optional)", value="karmaṇy evādhikāras te mā phaleṣu kadācana", key="qt_sanskrit_w")
        if st.button("Generate Quote Card", key="gen_qt"):
            st.session_state["preview_html"] = generate_quote_card(qt_text, qt_source, bg_b64, qt_sanskrit, card_w, card_h)
            st.session_state["preview_type"] = "quote"
            st.session_state["preview_dims"] = (card_w, card_h)
    with col2:
        if st.session_state.get("preview_type") == "quote" and st.session_state.get("preview_html"):
            st.markdown("#### Preview")
            st.components.v1.html(st.session_state["preview_html"], height=preview_h, scrolling=False)
            pw, ph = st.session_state.get("preview_dims", (1080, 1080))
            c1, c2 = st.columns(2)
            with c1: st.download_button("⬇ HTML", st.session_state["preview_html"], file_name="quote_card.html", mime="text/html", key="dl_qt_h")
            with c2:
                png = get_image_from_html(st.session_state["preview_html"], pw, ph)
                st.download_button("📸 PNG", png, file_name="quote_card.png", mime="image/png", key="dl_qt_p")

with tab2:
    col1, col2 = st.columns([1, 1])
    with col1:
        st.markdown("#### Carousel Slide")
        cs_tag = st.text_input("Series Tag", value="Lessons from Karna", key="cs_tag_w")
        cs_num = st.number_input("Slide Number", min_value=1, max_value=10, value=1, key="cs_num_w")
        cs_total = st.number_input("Total Slides", min_value=2, max_value=10, value=5, key="cs_total_w")
        cs_title = st.text_input("Slide Title", value="Birth does not define your worth. Your actions do.", key="cs_title_w")
        cs_body = st.text_area("Body Text", value="", height=120, key="cs_body_w")
        if st.button("Generate Carousel Slide", key="gen_cs"):
            st.session_state["preview_html"] = generate_carousel_slide(cs_tag, cs_num, cs_total, cs_title, cs_body, bg_b64, card_w, card_h)
            st.session_state["preview_type"] = "carousel"
            st.session_state["preview_dims"] = (card_w, card_h)
    with col2:
        if st.session_state.get("preview_type") == "carousel" and st.session_state.get("preview_html"):
            st.markdown("#### Preview")
            st.components.v1.html(st.session_state["preview_html"], height=preview_h, scrolling=False)
            pw, ph = st.session_state.get("preview_dims", (1080, 1080))
            c1, c2 = st.columns(2)
            with c1: st.download_button("⬇ HTML", st.session_state["preview_html"], file_name="carousel.html", mime="text/html", key="dl_cs_h")
            with c2:
                png = get_image_from_html(st.session_state["preview_html"], pw, ph)
                st.download_button("📸 PNG", png, file_name="carousel.png", mime="image/png", key="dl_cs_p")

with tab3:
    col1, col2 = st.columns([1, 1])
    with col1:
        st.markdown("#### Character Spotlight")
        ch_name = st.text_input("Character Name", value="Karna", key="ch_name_w")
        ch_title = st.text_input("Title / Epithet", value="The Loyal Outcast", key="ch_title_w")
        ch_traits = st.text_input("Traits (comma-separated)", value="Loyalty, Resilience, Generosity, Honour", key="ch_traits_w")
        ch_quote = st.text_area("Character Quote", value="", height=120, key="ch_quote_w")
        if st.button("Generate Character Spotlight", key="gen_ch"):
            traits = [t.strip() for t in ch_traits.split(",") if t.strip()]
            st.session_state["preview_html"] = generate_character_spotlight(ch_name, ch_title, traits, ch_quote, bg_b64, card_w, card_h)
            st.session_state["preview_type"] = "character"
            st.session_state["preview_dims"] = (card_w, card_h)
    with col2:
        if st.session_state.get("preview_type") == "character" and st.session_state.get("preview_html"):
            st.markdown("#### Preview")
            st.components.v1.html(st.session_state["preview_html"], height=preview_h, scrolling=False)
            pw, ph = st.session_state.get("preview_dims", (1080, 1080))
            c1, c2 = st.columns(2)
            with c1: st.download_button("⬇ HTML", st.session_state["preview_html"], file_name="character.html", mime="text/html", key="dl_ch_h")
            with c2:
                png = get_image_from_html(st.session_state["preview_html"], pw, ph)
                st.download_button("📸 PNG", png, file_name="character.png", mime="image/png", key="dl_ch_p")

with tab4:
    col1, col2 = st.columns([1, 1])
    with col1:
        st.markdown("#### Reflection Post")
        rf_tag = st.text_input("Tag Line", value="Ancient Lesson · Modern Life", key="rf_tag_w")
        rf_title = st.text_area("Title", value="Stop waiting\nfor permission.", height=80, key="rf_title_w")
        rf_body = st.text_area("Body", value="", height=120, key="rf_body_w")
        if st.button("Generate Reflection Post", key="gen_rf"):
            st.session_state["preview_html"] = generate_reflection(rf_tag, rf_title, rf_body, bg_b64, card_w, card_h)
            st.session_state["preview_type"] = "reflection"
            st.session_state["preview_dims"] = (card_w, card_h)
    with col2:
        if st.session_state.get("preview_type") == "reflection" and st.session_state.get("preview_html"):
            st.markdown("#### Preview")
            st.components.v1.html(st.session_state["preview_html"], height=preview_h, scrolling=False)
            pw, ph = st.session_state.get("preview_dims", (1080, 1080))
            c1, c2 = st.columns(2)
            with c1: st.download_button("⬇ HTML", st.session_state["preview_html"], file_name="reflection.html", mime="text/html", key="dl_rf_h")
            with c2:
                png = get_image_from_html(st.session_state["preview_html"], pw, ph)
                st.download_button("📸 PNG", png, file_name="reflection.png", mime="image/png", key="dl_rf_p")

with tab5:
    col1, col2 = st.columns([1, 1])
    with col1:
        st.markdown("#### List / Tips Post")
        ls_tag = st.text_input("Tag Line", value="5 Things Krishna Teaches Us", key="ls_tag_w")
        ls_title = st.text_input("Title", value="About Handling Pressure", key="ls_title_w")
        ls_items = st.text_area("List Items (one per line)", value="Act without attachment to outcome\nSee the eternal, not just the moment\nKnow your Dharma and hold it", height=150, key="ls_items_w")
        if st.button("Generate List Post", key="gen_ls"):
            items = [i.strip() for i in ls_items.split("\n") if i.strip()]
            st.session_state["preview_html"] = generate_list_post(ls_tag, ls_title, items, bg_b64, card_w, card_h)
            st.session_state["preview_type"] = "list"
            st.session_state["preview_dims"] = (card_w, card_h)
    with col2:
        if st.session_state.get("preview_type") == "list" and st.session_state.get("preview_html"):
            st.markdown("#### Preview")
            st.components.v1.html(st.session_state["preview_html"], height=preview_h, scrolling=False)
            pw, ph = st.session_state.get("preview_dims", (1080, 1080))
            c1, c2 = st.columns(2)
            with c1: st.download_button("⬇ HTML", st.session_state["preview_html"], file_name="list.html", mime="text/html", key="dl_ls_h")
            with c2:
                png = get_image_from_html(st.session_state["preview_html"], pw, ph)
                st.download_button("📸 PNG", png, file_name="list.png", mime="image/png", key="dl_ls_p")

with tab6:
    col1, col2 = st.columns([1, 1])
    with col1:
        st.markdown("#### Series Cover")
        sc_label = st.text_input("Series Label", value="Carousel Series", key="sc_label_w")
        sc_num = st.number_input("Series Number", min_value=1, max_value=99, value=1, key="sc_num_w")
        sc_title = st.text_area("Cover Title", value="Why Arjuna's Doubt\nWas His Greatest Weapon", height=80, key="sc_title_w")
        sc_sub = st.text_input("Subtitle", value="5 lessons on when hesitation becomes wisdom", key="sc_sub_w")
        if st.button("Generate Series Cover", key="gen_sc"):
            st.session_state["preview_html"] = generate_series_cover(sc_label, sc_num, sc_title, sc_sub, bg_b64, card_w, card_h)
            st.session_state["preview_type"] = "cover"
            st.session_state["preview_dims"] = (card_w, card_h)
    with col2:
        if st.session_state.get("preview_type") == "cover" and st.session_state.get("preview_html"):
            st.markdown("#### Preview")
            st.components.v1.html(st.session_state["preview_html"], height=preview_h, scrolling=False)
            pw, ph = st.session_state.get("preview_dims", (1080, 1080))
            c1, c2 = st.columns(2)
            with c1: st.download_button("⬇ HTML", st.session_state["preview_html"], file_name="series_cover.html", mime="text/html", key="dl_sc_h")
            with c2:
                png = get_image_from_html(st.session_state["preview_html"], pw, ph)
                st.download_button("📸 PNG", png, file_name="series_cover.png", mime="image/png", key="dl_sc_p")

# ─── Footer ───
st.markdown("""
<hr style="border-color: rgba(201,146,42,0.15); margin: 40px 60px 20px;">
<div style="text-align:center; padding: 20px 0 40px;">
  <div style="font-family:'Cinzel Decorative',serif; font-size:22px; background:linear-gradient(135deg,#F5D78E,#C9922A); -webkit-background-clip:text; -webkit-text-fill-color:transparent; letter-spacing:3px;">MAHABHARATA MINDSET</div>
  <div style="margin-top:8px; font-family:'Cinzel',serif; font-size:8px; letter-spacing:4px; text-transform:uppercase; color:rgba(201,146,42,.25);">Post Generator · @TheMahabharataMindset</div>
</div>
""", unsafe_allow_html=True)

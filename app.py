import os
import json
import sqlite3
import random
import asyncio
from functools import partial
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
import google.generativeai as genai
from dotenv import load_dotenv
load_dotenv()

import fal_client
fal_client.api_key = os.environ.get("FAL_KEY", "")

genai.configure(api_key=os.environ.get("GEMINI_API_KEY", ""))
gemini_model = genai.GenerativeModel("gemini-2.5-flash")

DB_PATH = "stories.db"

THEMES = {
    "princesas": {
        "label": "Princesas",
        "emoji": "👸",
        "setting": "a magical fairy tale kingdom with shimmering castles, enchanted gardens, and sparkling fairy dust",
        "style": "soft pastel watercolor, fairy tale illustration, golden accents, dreamy and magical",
    },
    "espacio": {
        "label": "Espacio",
        "emoji": "🚀",
        "setting": "outer space with colorful planets, twinkling stars, friendly aliens, and a shiny rocket ship",
        "style": "vibrant space illustration, deep blues and purples, glowing stars, cosmic and adventurous",
    },
    "bosque": {
        "label": "Bosque Mágico",
        "emoji": "🌲",
        "setting": "an enchanted forest with talking animals, glowing mushrooms, sparkling streams, and ancient wise trees",
        "style": "lush green watercolor, warm earthy tones, dappled sunlight, whimsical and cozy",
    },
    "piratas": {
        "label": "Piratas",
        "emoji": "⚓",
        "setting": "a treasure-filled pirate adventure on the high seas with friendly dolphins, mysterious islands, and a colorful ship",
        "style": "adventure illustration, ocean blues and sandy yellows, nautical details, bold and cheerful",
    },
    "dinosaurios": {
        "label": "Dinosaurios",
        "emoji": "🦕",
        "setting": "a prehistoric jungle world with friendly giant dinosaurs, lush ferns, volcanoes, and hidden caves",
        "style": "lush jungle illustration, rich greens and earth tones, prehistoric plants, fun and exciting",
    },
    "oceano": {
        "label": "Océano",
        "emoji": "🌊",
        "setting": "a magical underwater world with colorful coral reefs, friendly fish, playful dolphins, and a sunken treasure chest",
        "style": "underwater watercolor, turquoise and coral colors, shimmering light rays, peaceful and magical",
    },
    "superheroes": {
        "label": "Superhéroes",
        "emoji": "🦸",
        "setting": "a cheerful city where kind superheroes help everyone, with flying heroes, colorful costumes, and happy citizens",
        "style": "bright comic book illustration, bold primary colors, action lines, fun and energetic",
    },
    "granja": {
        "label": "Granja",
        "emoji": "🐄",
        "setting": "a cozy sunny farm with friendly cows, sheep, chickens, pigs, a red barn, and golden sunflower fields",
        "style": "warm farm illustration, sunny yellows and soft greens, cheerful and rustic, wholesome and cozy",
    },
    "dragones": {
        "label": "Dragones",
        "emoji": "🐉",
        "setting": "a magical land where friendly dragons fly through rainbow skies, and crystal mountains hold ancient secrets",
        "style": "fantasy watercolor illustration, jewel tones and shimmering scales, magical and imaginative",
    },
    "circo": {
        "label": "Circo",
        "emoji": "🎪",
        "setting": "a spectacular magical circus with acrobats, funny clowns, trained animals, and a big colorful tent",
        "style": "vibrant circus illustration, bright reds and golds, striped patterns, festive and joyful",
    },
    "hadas": {
        "label": "Hadas",
        "emoji": "🧚",
        "setting": "a tiny fairy world hidden in a flower garden, with glowing fairy houses, dewdrop lakes, and petal bridges",
        "style": "delicate fantasy watercolor, soft pinks and greens, glowing lights, ethereal and gentle",
    },
    "aventura": {
        "label": "Gran Aventura",
        "emoji": "🗺️",
        "setting": "an exciting adventure across mountains, rivers, and hidden temples searching for a legendary treasure",
        "style": "adventure map illustration, warm browns and greens, compass and map details, bold and exciting",
    },
}

def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS stories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            protagonist TEXT NOT NULL,
            theme TEXT NOT NULL,
            plot_summary TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS saved_stories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            protagonist TEXT NOT NULL,
            theme TEXT NOT NULL,
            pages_json TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield

app = FastAPI(lifespan=lifespan)

class GenerateRequest(BaseModel):
    protagonist: str
    theme: str
    character_description: str = ""

def get_previous_summaries(protagonist: str, theme: str) -> list[str]:
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        "SELECT plot_summary FROM stories WHERE protagonist = ? AND theme = ? ORDER BY created_at DESC LIMIT 10",
        (protagonist.lower().strip(), theme)
    ).fetchall()
    conn.close()
    return [row[0] for row in rows]

def save_story(protagonist: str, theme: str, plot_summary: str):
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "INSERT INTO stories (protagonist, theme, plot_summary) VALUES (?, ?, ?)",
        (protagonist.lower().strip(), theme, plot_summary)
    )
    conn.commit()
    conn.close()

def _generate_story_text(protagonist: str, theme: str, previous_summaries: list[str], character_description: str = "") -> dict:
    theme_data = THEMES[theme]

    if character_description:
        char_desc_instruction = f"MANDATORY: The parent has described the character as: '{character_description}'. You MUST respect ALL physical traits exactly as described (hair color, eye color, skin tone, etc). Translate to English and add clothing/accessories, but NEVER change the described physical features."
    else:
        char_desc_instruction = "Invent appropriate details: approximate age look, hair color and style, eye color, skin tone, clothing colors and style, any accessories."

    avoid_section = ""
    if previous_summaries:
        summaries_text = "\n".join(f"- {s}" for s in previous_summaries)
        avoid_section = f"""
IMPORTANT - STORIES ALREADY TOLD: The following stories have been generated before for {protagonist} in this theme.
You MUST write a completely different story with a different plot, different challenge, different supporting characters, and different resolution. Do NOT reuse these plots:
{summaries_text}
"""

    prompt = f"""Write a children's story in Spanish for children aged 3-4 years old.

Protagonist name: {protagonist}
Setting: {theme_data['setting']}
Character description instruction: {char_desc_instruction}
{avoid_section}

Requirements:
- Exactly 5 pages
- Total ~450 words (around 90 words per page)
- Language STRICTLY for 3-4 year olds: very short sentences (max 10 words each), only everyday words a toddler knows, no subordinate clauses, lots of repetition and sounds (like "¡Splash!", "¡Bum!", "¡Oh!"), describe emotions simply ("estaba muy contento", "tenía mucho miedo")
- Warm, positive tone
- The protagonist faces a very simple small challenge and overcomes it with kindness or courage
- Include 1-2 friendly animal characters that speak simply
- Happy ending with a simple moral lesson stated in one short sentence
- Each page ends making you curious about what happens next

Return ONLY valid JSON with this exact structure (no markdown, no explanation):
{{
  "title": "Story title in Spanish (creative, 4-8 words)",
  "character_description": "Detailed visual description of the protagonist in English for illustration. CRITICAL: if physical traits were provided above, copy them exactly without changing anything. Add clothing and accessories. Be very specific. Example: A cheerful 4-year-old girl with curly auburn hair in two pigtails, bright hazel eyes, light brown skin, wearing a red polka-dot dress and small red shoes",
  "plot_summary": "2 sentence plot summary in Spanish for internal tracking",
  "pages": [
    {{
      "page_number": 1,
      "text": "Story text in Spanish for this page (~90 words)",
      "scene_description": "Scene description in English for illustration: what is happening, where, background details, mood, lighting. Do NOT describe character appearance here."
    }},
    {{
      "page_number": 2,
      "text": "...",
      "scene_description": "..."
    }},
    {{
      "page_number": 3,
      "text": "...",
      "scene_description": "..."
    }},
    {{
      "page_number": 4,
      "text": "...",
      "scene_description": "..."
    }},
    {{
      "page_number": 5,
      "text": "...",
      "scene_description": "..."
    }}
  ]
}}"""

    response = gemini_model.generate_content(prompt)

    content = response.text.strip()
    if "```json" in content:
        content = content.split("```json")[1].split("```")[0].strip()
    elif "```" in content:
        content = content.split("```")[1].split("```")[0].strip()

    return json.loads(content)

def _generate_image(scene_description: str, character_description: str, theme: str, seed: int) -> str:
    style = THEMES[theme]["style"]

    prompt = (
        f"Children's picture book illustration. "
        f"Main character: {character_description}. "
        f"Scene: {scene_description}. "
        f"Art style: {style}. "
        "Cute, friendly, warm atmosphere. Suitable for ages 3-4. "
        "High quality storybook illustration. No text or letters in the image."
    )

    result = fal_client.subscribe(
        "fal-ai/flux-pro/v1.1",
        arguments={
            "prompt": prompt,
            "seed": seed,
            "image_size": "landscape_4_3",
            "num_inference_steps": 28,
            "guidance_scale": 3.5,
            "num_images": 1,
            "safety_tolerance": "2",
        }
    )

    return result["images"][0]["url"]

def _sse(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"

@app.post("/api/generate")
async def generate_story(request: GenerateRequest):
    protagonist = request.protagonist.strip()
    theme = request.theme

    if not protagonist:
        raise HTTPException(status_code=400, detail="El nombre del protagonista es requerido")
    if theme not in THEMES:
        raise HTTPException(status_code=400, detail="Temática no válida")

    async def stream():
        loop = asyncio.get_event_loop()
        try:
            yield _sse({"type": "status", "message": "✍️ Escribiendo tu cuento mágico..."})

            previous_summaries = get_previous_summaries(protagonist, theme)
            story = await loop.run_in_executor(
                None, partial(_generate_story_text, protagonist, theme, previous_summaries, request.character_description)
            )

            seed = random.randint(10000, 999999)

            yield _sse({"type": "story_start", "title": story["title"]})

            pages_done = []
            for page in story["pages"]:
                n = page["page_number"]
                yield _sse({"type": "status", "message": f"🎨 Dibujando ilustración {n} de 5..."})

                image_url = await loop.run_in_executor(
                    None,
                    partial(_generate_image, page["scene_description"], story["character_description"], theme, seed + n)
                )

                page_data = {
                    "page_number": n,
                    "text": page["text"],
                    "image_url": image_url,
                }
                pages_done.append(page_data)
                yield _sse({"type": "page", "page": page_data})

            await loop.run_in_executor(
                None, partial(save_story, protagonist, theme, story["plot_summary"])
            )

            yield _sse({"type": "done"})

        except json.JSONDecodeError as e:
            yield _sse({"type": "error", "message": f"Error al generar el cuento. Por favor, inténtalo de nuevo."})
        except Exception as e:
            yield _sse({"type": "error", "message": f"Error inesperado: {str(e)}"})

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        }
    )

@app.get("/api/themes")
async def get_themes():
    return {k: {"label": v["label"], "emoji": v["emoji"]} for k, v in THEMES.items()}

class SaveRequest(BaseModel):
    title: str
    protagonist: str
    theme: str
    pages: list

@app.post("/api/save")
async def save_story_endpoint(request: SaveRequest):
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "INSERT INTO saved_stories (title, protagonist, theme, pages_json) VALUES (?, ?, ?, ?)",
        (request.title, request.protagonist, request.theme, json.dumps(request.pages, ensure_ascii=False))
    )
    conn.commit()
    conn.close()
    return {"ok": True}

@app.get("/api/saved")
async def list_saved():
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        "SELECT id, title, protagonist, theme, created_at FROM saved_stories ORDER BY created_at DESC"
    ).fetchall()
    conn.close()
    return [{"id": r[0], "title": r[1], "protagonist": r[2], "theme": r[3], "created_at": r[4]} for r in rows]

@app.get("/api/saved/{story_id}")
async def get_saved_story(story_id: int):
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute(
        "SELECT id, title, protagonist, theme, pages_json, created_at FROM saved_stories WHERE id = ?",
        (story_id,)
    ).fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="Cuento no encontrado")
    return {"id": row[0], "title": row[1], "protagonist": row[2], "theme": row[3], "pages": json.loads(row[4]), "created_at": row[5]}

@app.delete("/api/saved/{story_id}")
async def delete_saved_story(story_id: int):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("DELETE FROM saved_stories WHERE id = ?", (story_id,))
    conn.commit()
    conn.close()
    return {"ok": True}

@app.get("/")
async def serve_index():
    return FileResponse("static/index.html")

app.mount("/static", StaticFiles(directory="static"), name="static")
